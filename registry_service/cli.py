"""Optional service startup and signed GitHub catalog import. No remote writes."""
from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

from core.registry.codec import RegistryError, decode, require
from core.registry.trust import TrustedCatalog
from registry_service.api import create_app
from registry_service.auth import Auth, Provider
from registry_service.store import Store


def load_service(config: dict, *, now: int):
    require(isinstance(config, dict) and set(config) == {"schema", "origin", "private_dir", "trusted_root",
        "catalog_file", "artifact_dir", "target", "providers"} and config["schema"] == "vool.registry.service.v1",
        "unknown service configuration")
    private = Path(config["private_dir"]).resolve()
    private.mkdir(mode=0o700, parents=True, exist_ok=True)
    require(private.stat().st_mode & 0o077 == 0, "private directory permissions must be 0700")
    root = decode(Path(config["trusted_root"]).read_bytes(), 65536)
    trust = TrustedCatalog(root, private / "trust.sqlite", now=now)
    store = Store(private / "accounts.sqlite")
    directory = decode(Path(config["catalog_file"]).read_bytes())
    require(isinstance(directory, dict) and isinstance(directory.get("entries"), list) and "registry" in directory,
            "canonical catalog has no signed reviewed projection")
    trust.accept(directory["registry"], now=now)
    for envelope in directory["registry"]["signed"]["entries"]:
        entry = envelope["signed"]
        info = entry["metadata"]
        try:
            trust.resolve(info["id"], info["version"], target=config["target"], now=now)
        except RegistryError:
            continue
        raw = (Path(config["artifact_dir"]) / (entry["artifact_sha256"] + ".json")).read_bytes()
        store.publish(trust, info["id"], info["version"], config["target"], raw, now=now)
    store.prune(now=now)
    providers = []
    require(isinstance(config["providers"], list), "invalid provider registrations")
    for name in config["providers"]:
        require(name in {"github", "google", "apple"}, "unknown login provider")
        prefix = "VOOL_REGISTRY_" + name.upper()
        providers.append(Provider(name, os.environ.get(prefix + "_CLIENT_ID", ""), os.environ.get(prefix + "_CLIENT_SECRET", "")))
    auth = Auth(store, config["origin"], providers)
    return store, trust, auth


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["check-config", "serve"])
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--port", type=int, default=8791)
    args = parser.parse_args(argv)
    trust = None
    try:
        config = decode(args.config.read_bytes(), 65536)
        store, trust, auth = load_service(config, now=int(time.time()))
        if args.command == "check-config":
            print("Configuration, signed catalog and artifact cache verified.")
            return 0
        import uvicorn

        # Access and error logs can contain addresses/query strings. The service
        # has no per-request logs; operational health is read via JSON endpoints.
        log_config = {"version": 1, "disable_existing_loggers": False,
            "handlers": {"discard": {"class": "logging.NullHandler"}},
            "loggers": {name: {"handlers": ["discard"], "propagate": False}
                        for name in ("uvicorn", "uvicorn.error", "uvicorn.access")}}
        discovery = decode(Path(config["catalog_file"]).read_bytes())["entries"]
        uvicorn.run(create_app(store, auth, trust, config["target"], discovery=discovery), host="127.0.0.1", port=args.port,
                    access_log=False, log_config=log_config, proxy_headers=False)
        return 0
    except (RegistryError, OSError, ValueError, KeyError, TypeError):
        print("Registry startup refused: incomplete configuration, stale signatures or missing immutable artifacts.")
        return 1
    finally:
        if trust is not None:
            trust.close()


if __name__ == "__main__":
    raise SystemExit(main())
