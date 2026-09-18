"""Generate the publisher Ed25519 keypair for signing update releases.

The PUBLIC half is meant to be pinned in config/release/trusted_publisher_keys.json.
The PRIVATE half must NEVER live in this repository: this tool refuses to write inside
the repo and prints the private key only when --print-private is given explicitly, so
release engineering can pipe it into a secret store.

Usage:
    python -m installer.gen_publisher_keypair --key-id release-2026-09 [--out-dir ~/.vool-release-secrets]

Secret-hygiene: the key is generated in-process with `cryptography` and either printed
(to stdout, only on request) or written to the given out-of-repo directory with 0600.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def generate_keypair() -> tuple[str, str]:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    key = Ed25519PrivateKey.generate()
    return key.private_bytes_raw().hex(), key.public_key().public_bytes_raw().hex()


def _is_inside_repo(path: Path) -> bool:
    try:
        path.resolve().relative_to(REPO_ROOT)
        return True
    except ValueError:
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vool-gen-publisher-keypair")
    parser.add_argument("--key-id", required=True, help="pin id for the public key (e.g. release-2026-09)")
    parser.add_argument("--out-dir", default="", help="directory for the key files (MUST be outside this repo)")
    parser.add_argument("--print-private", action="store_true", help="print the private key to stdout (secret!)")
    args = parser.parse_args(argv)

    private_hex, public_hex = generate_keypair()
    print(f"key_id:    {args.key_id}")
    print(f"public:    {public_hex}")
    print("pin the public half in config/release/trusted_publisher_keys.json as:")
    print(f'  "{args.key_id}": "{public_hex}"')

    if args.print_private:
        print(f"private:   {private_hex}  (SECRET — store outside the repo; never commit)")

    if args.out_dir:
        out_dir = Path(args.out_dir).expanduser()
        if _is_inside_repo(out_dir):
            print("ERROR: refusing to write the private key inside this repository.", file=sys.stderr)
            return 1
        out_dir.mkdir(parents=True, exist_ok=True)
        private_file = out_dir / f"{args.key_id}.private.hex"
        private_file.write_text(private_hex + "\n", encoding="utf-8")
        private_file.chmod(0o600)
        (out_dir / f"{args.key_id}.public.hex").write_text(public_hex + "\n", encoding="utf-8")
        print(f"wrote keypair to {out_dir} (private file mode 0600)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
