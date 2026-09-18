"""Extract pinned browser distributions without executing package install scripts."""
import argparse
import base64
import hashlib
import json
import tarfile
from pathlib import Path

PACKAGES = {
    "mermaid": {
        "version": "12.0.0",
        "url": "https://registry.npmjs.org/mermaid/-/mermaid-12.0.0.tgz",
        "integrity": "/wQXC9iBxoGV8p3erbvaXs9h77VyLDBH6GdayVjj3hEcSQhFU4N1WUhUppotCEqlIxI2pRMwjwBSwTB1MfZBgQ==",
        "files": {"package/dist/mermaid.min.js": "mermaid-12.0.0.min.js", "package/LICENSE": "mermaid-LICENSE"},
    },
    "chart": {
        "version": "4.5.1",
        "url": "https://registry.npmjs.org/chart.js/-/chart.js-4.5.1.tgz",
        "integrity": "GIjfiT9dbmHRiYi6Nl2yFCq7kkwdkp1W/lp2J99rX0yo9tgJGn3lKQATztIjb5tVtevcBtIdICNWqlq5+E8/Pw==",
        "files": {"package/dist/chart.umd.min.js": "chart-4.5.1.min.js", "package/LICENSE.md": "chart-LICENSE.md"},
    },
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archives", type=Path, help="Directory containing mermaid.tgz and chart.tgz")
    args = parser.parse_args()
    destination = Path(__file__).resolve().parents[1] / "core/web_assets"
    destination.mkdir(exist_ok=True)
    manifest = {}
    for name, package in PACKAGES.items():
        archive = args.archives / f"{name}.tgz"
        digest = base64.b64encode(hashlib.sha512(archive.read_bytes()).digest()).decode()
        if digest != package["integrity"]:
            raise ValueError(f"{name}: registry integrity mismatch")
        files = {}
        with tarfile.open(archive) as source:
            for member, filename in package["files"].items():
                entry = source.getmember(member)
                if not entry.isfile():
                    raise ValueError(f"{name}: expected ordinary file")
                data = source.extractfile(entry).read()
                (destination / filename).write_bytes(data)
                files[filename] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
        manifest[name] = {"version": package["version"], "url": package["url"],
                          "integrity": "sha512-" + digest, "license": "MIT", "files": files}
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
