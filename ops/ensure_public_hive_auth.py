from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.public_hive_bridge import ensure_public_hive_auth


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Ensure VOOL public Hive auth/bootstrap is present.")
    parser.add_argument("--project-root", default=str(REPO_ROOT), help="Project root used to resolve bundled config.")
    parser.add_argument("--target-path", default="", help="Optional destination path for the runtime bootstrap file.")
    parser.add_argument("--watch-host", default="", help="Optional public Hive watch host for SSH sync.")
    parser.add_argument("--watch-user", default="root", help="SSH username used for public Hive auth sync.")
    parser.add_argument(
        "--remote-config-path",
        default="",
        help="Remote config path used when public Hive auth must be synced over SSH. Typical path: /etc/vool-hive-mind/watch-config.json.",
    )
    parser.add_argument(
        "--require-auth",
        action="store_true",
        help="Fail if writes require auth and auth cannot be hydrated.",
    )
    parser.add_argument("--json", action="store_true", help="Emit the full result payload as JSON.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    target_path = Path(args.target_path).expanduser() if str(args.target_path).strip() else None
    result = ensure_public_hive_auth(
        project_root=args.project_root,
        target_path=target_path,
        watch_host=args.watch_host,
        watch_user=args.watch_user,
        remote_config_path=args.remote_config_path,
        require_auth=bool(args.require_auth),
    )
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        status = str(result.get("status") or "unknown")
        target = str(result.get("target_path") or "")
        message = f"status={status}"
        if target:
            message += f" target_path={target}"
        print(message)
        watch_host = str(result.get("watch_host") or "").strip()
        if watch_host:
            print(f"watch_host={watch_host}")
        suggested_remote_config_path = str(result.get("suggested_remote_config_path") or "").strip()
        if suggested_remote_config_path:
            print(f"suggested_remote_config_path={suggested_remote_config_path}")
        suggested_command = str(result.get("suggested_command") or "").strip()
        if suggested_command:
            print(f"next_step={suggested_command}")
        error = str(result.get("error") or "").strip()
        if error:
            print(f"error={error}")
    return 0 if bool(result.get("ok")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
