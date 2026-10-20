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
    require(isinstance(config, dict) and set(config) - {"github_import", "authority"} == {"schema", "origin", "private_dir",
        "trusted_root", "catalog_file", "artifact_dir", "target", "providers"} and
        config["schema"] == "vool.registry.service.v1" and type(config.get("github_import", False)) is bool and
        type(config.get("authority", False)) is bool, "unknown service configuration")
    private = Path(config["private_dir"]).resolve()
    private.mkdir(mode=0o700, parents=True, exist_ok=True)
    require(private.stat().st_mode & 0o077 == 0, "private directory permissions must be 0700")
    root = decode(Path(config["trusted_root"]).read_bytes(), 65536)
    trust = TrustedCatalog(root, private / "trust.sqlite", now=now)
    store = Store(private / "accounts.sqlite")
    directory = decode(Path(config["catalog_file"]).read_bytes())
    require(isinstance(directory, dict) and isinstance(directory.get("entries"), list) and
            ("registry" in directory or config.get("authority") is True),
            "canonical catalog has no signed reviewed projection")
    online = None
    if config.get("authority") is True:
        # The signing Mac publishes through this service; its online key re-stamps the list's freshness.
        from registry_service.authority import OnlineCatalog, load_online_key

        online = OnlineCatalog(store, trust, load_online_key(private / "catalog.key"), Path(config["catalog_file"]),
                               config["target"])
    try:
        if "registry" in directory:
            trust.accept(directory["registry"], now=now)
    except RegistryError:
        # A list that went stale while the service was down is re-stamped, if the Mac's signature still holds.
        require(online is not None, "stale catalog")
        online.refresh(now=now)
    # With the signing Mac, a first start has no list yet: the catalog stays empty until the Mac's first run.
    for envelope in (trust.current or {"entries": []})["entries"]:
        entry = envelope["signed"]
        info = entry["metadata"]
        try:
            trust.resolve_any(info["id"], info["version"], now=now)
        except RegistryError:
            continue
        raw = store.admitted_artifact(entry["artifact_sha256"])
        if raw is None:
            cached = Path(config["artifact_dir"]) / (entry["artifact_sha256"] + ".json")
            if online is not None and not cached.exists():
                continue  # its bytes left with a deleted account before they were published; it is not served
            raw = cached.read_bytes()
        store.publish(trust, info["id"], info["version"], None, raw, now=now)
    store.prune(now=now)
    providers = []
    require(isinstance(config["providers"], list), "invalid provider registrations")
    for name in config["providers"]:
        require(name in {"github", "google", "apple"}, "unknown login provider")
        prefix = "VOOL_REGISTRY_" + name.upper()
        providers.append(Provider(name, os.environ.get(prefix + "_CLIENT_ID", ""), os.environ.get(prefix + "_CLIENT_SECRET", "")))
    auth = Auth(store, config["origin"], providers)
    return store, trust, auth, online


MODERATION = ("comments-held", "comment-publish", "comment-remove", "mute", "unmute", "pictures-pending",
              "reports-open", "reports-close", "skill-pause", "skill-resume", "reporter-mute")


def moderate(args) -> int:
    """Operator-only moderation against the private store. There is no HTTP route for this."""
    import json

    from registry_service import community, reports

    try:
        config = decode(args.config.read_bytes(), 65536)
        require(isinstance(config, dict) and config.get("schema") == "vool.registry.service.v1", "unknown service configuration")
        path = Path(config["private_dir"]).resolve() / "accounts.sqlite"
        require(path.is_file(), "private store not found")
        store = Store(path)
        if args.command == "pictures-pending":
            from registry_service.curation import load_curation, picture_approvals

            approvals = picture_approvals(store.marks().get("curation", load_curation()))
            pending = [item for item in store.published_pictures() if (item["id"], item["sha256"]) not in approvals]
            if args.target:
                # Write each waiting picture into a new folder so a person can look at the exact file.
                folder = Path(args.target)
                require(not folder.exists(), "choose a new folder for the waiting pictures")
                folder.mkdir(mode=0o700)
                for sha256 in dict.fromkeys(item["sha256"] for item in pending):
                    with (folder / (sha256 + ".png")).open("xb") as handle:
                        handle.write(store.picture(sha256)[1])
            print(json.dumps(pending, indent=2))
        elif args.command == "reports-open":
            print(json.dumps(reports.open_reports(store), indent=2))
        elif args.command == "reports-close":
            reports.close(store, args.target)
            print("Done.")
        elif args.command == "skill-pause":
            reports.pause(store, args.target, now=int(time.time()))
            print("Done.")
        elif args.command == "skill-resume":
            reports.resume(store, args.target)
            print("Done.")
        elif args.command == "reporter-mute":
            reports.mute_reporter(store, args.target, now=int(time.time()))
            print("Done.")
        elif args.command == "comments-held":
            print(json.dumps(community.held(store), indent=2))
        elif args.command in {"comment-publish", "comment-remove"}:
            community.moderate(store, args.target, args.command.split("-")[1])
            print("Done.")
        else:
            community.mute(store, args.target, args.command == "mute", now=int(time.time()))
            print("Done.")
        return 0
    except (RegistryError, OSError, ValueError, KeyError, TypeError) as exc:
        print("Moderation refused: " + (str(exc) if isinstance(exc, RegistryError) else "invalid configuration"))
        return 1


def catalog_key(args) -> int:
    """Create the service's online catalog key once and print its public half for the Mac's setup."""
    from registry_service.authority import create_online_key, keyid

    try:
        config = decode(args.config.read_bytes(), 65536)
        require(isinstance(config, dict) and config.get("schema") == "vool.registry.service.v1", "unknown service configuration")
        private = Path(config["private_dir"]).resolve()
        private.mkdir(mode=0o700, parents=True, exist_ok=True)
        require(private.stat().st_mode & 0o077 == 0, "private directory permissions must be 0700")
        key = create_online_key(private / "catalog.key")
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

        public = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
        print("Online catalog key " + keyid(key) + "\npublic: " + public + "\nGive the public line to the Mac's setup.")
        return 0
    except (RegistryError, OSError, ValueError, KeyError, TypeError) as exc:
        print("Catalog key refused: " + (str(exc) if isinstance(exc, RegistryError) else "invalid configuration"))
        return 1


def restamp(online, *, every: int = 600) -> None:
    """Keep the list fresh between the Mac's runs, for as long as the Mac's own signature allows."""
    import threading

    def loop():
        while True:
            time.sleep(every)
            try:
                online.refresh(now=int(time.time()))
            except Exception:  # one bad pass must not end the re-stamping for good
                continue  # stale: installs stop until the Mac signs again; never serve an unsigned list

    threading.Thread(target=loop, name="catalog-restamp", daemon=True).start()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["check-config", "serve", "catalog-key", *MODERATION])
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--port", type=int, default=8791)
    parser.add_argument("--target", default="", help="comment id, report id, account namespace or skill id (publisher/name) "
                        "for moderation commands; for pictures-pending, a new folder to write the waiting pictures into")
    args = parser.parse_args(argv)
    trust = None
    if args.command in MODERATION:
        return moderate(args)
    if args.command == "catalog-key":
        return catalog_key(args)
    try:
        config = decode(args.config.read_bytes(), 65536)
        store, trust, auth, online = load_service(config, now=int(time.time()))
        from registry_service.curation import load_curation

        curation = load_curation()  # a bad curation file stops the service here, not at serve time
        if args.command == "check-config":
            print("Configuration, signed catalog, artifact cache and curation verified.")
            return 0
        import uvicorn

        # Access and error logs can contain addresses/query strings. The service
        # has no per-request logs; operational health is read via JSON endpoints.
        log_config = {"version": 1, "disable_existing_loggers": False,
            "handlers": {"discard": {"class": "logging.NullHandler"}},
            "loggers": {name: {"handlers": ["discard"], "propagate": False}
                        for name in ("uvicorn", "uvicorn.error", "uvicorn.access")}}
        discovery = decode(Path(config["catalog_file"]).read_bytes())["entries"]
        # GitHub import is off unless the operator turns it on; it reads public GitHub only.
        github = None
        if config.get("github_import") is True:
            from registry_service.github_source import GitHubSource

            github = GitHubSource()
        if online is not None:
            restamp(online)
        uvicorn.run(create_app(store, auth, trust, config["target"], discovery=discovery, github=github, curation=curation,
                               authority=online),
                    host="127.0.0.1", port=args.port,
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
