from __future__ import annotations

import argparse
import json
import shlex
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from installer.llamacpp_local import download_llamacpp_model, write_llamacpp_local_config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="provision_llamacpp_local")
    parser.add_argument("--runtime-home", required=True)
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--emit-shell-env", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.download:
        config = download_llamacpp_model(runtime_home=args.runtime_home)
    else:
        config, _ = write_llamacpp_local_config(runtime_home=args.runtime_home)

    if args.emit_shell_env:
        for key, value in config.env_exports().items():
            print(f"export {key}={shlex.quote(str(value))}")
        return 0
    if args.json:
        print(json.dumps(config.to_dict(), indent=2, ensure_ascii=False))
        return 0
    print(str(Path(args.runtime_home).expanduser().resolve() / "config" / "llamacpp-local.json"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
