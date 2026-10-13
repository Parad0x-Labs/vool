"""Offline queue review/export commands; never execute uploads or hold signing keys."""
from __future__ import annotations

import argparse
import time
from pathlib import Path

from core.registry.codec import RegistryError, canonical, decode
from core.registry.trust import root_document, verify
from registry_service.store import Store
from tools.registry_ci import bounded_file


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["review", "export", "prune"])
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--id")
    parser.add_argument("--root", type=Path)
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--review", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        now = int(time.time())
        store = Store(args.database)
        if args.command == "prune":
            store.prune(now=now)
            print("Expired private payloads and sessions pruned.")
            return 0
        root_envelope = decode(bounded_file(args.root))
        root = root_document(root_envelope["signed"], now)
        verify(root_envelope, root, "root")
        policy = decode(bounded_file(args.policy))
        if args.command == "review":
            store.review(args.id, decode(bounded_file(args.review)), policy, root, now=now)
            print("Signed review processed; read the author's private submission status.")
            return 0
        raw, review = store.export_submission(args.id, policy, root, now=now)
        from core.registry.artifact import inspect

        sha = inspect(raw)["sha256"]
        args.output.mkdir(mode=0o700)
        with (args.output / (sha + ".json")).open("xb") as output:
            output.write(raw)
        with (args.output / (sha + ".review.json")).open("xb") as output:
            output.write(canonical(review))
        print("Exact consented candidate exported for a GitHub PR; no publication performed.")
        return 0
    except (RegistryError, OSError, ValueError, TypeError, KeyError, AttributeError):
        print("Queue operation refused: verify the exact input, private consent, policy and review signatures.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
