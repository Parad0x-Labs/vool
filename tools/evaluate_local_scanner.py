#!/usr/bin/env python3
"""Evaluate the VOOL local scanner on the labeled corpus, bundled sources, and
optionally the pinned upstream Eyebrow engine on identical bytes.

Evidence tool, not product code. Labels come from the frozen manifest; the
alert threshold is any finding with confidence >= medium. The upstream
comparison lays each sample out as a discovered claude-code skill and runs the
pinned Eyebrow binary with a disposable HOME and a black-hole proxy so no
sample URL is ever followed.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.local_scan import scan_bytes

ALERT_CONFIDENCE = {"medium", "high"}


def local_result(content: bytes) -> dict:
    report = scan_bytes("sample", content)
    alerts = [f for f in report["findings"] if f["confidence"] in ALERT_CONFIDENCE]
    informational = [f for f in report["findings"] if f["confidence"] not in ALERT_CONFIDENCE]
    return {"status": report["status"], "alert": bool(alerts),
            "alert_rules": sorted({f["ruleId"] for f in alerts}),
            "informational_rules": sorted({f["ruleId"] for f in informational}),
            "finding_count": len(report["findings"]), "duration_ms": report["duration_ms"]}


def eyebrow_result(binary: pathlib.Path, scratch: pathlib.Path, name: str, content: bytes) -> dict:
    project = scratch / "eb" / name
    layout = project / ".claude" / "skills" / name
    layout.mkdir(parents=True, exist_ok=True)
    (layout / "SKILL.md").write_bytes(content)
    home = scratch / "eb-home"
    home.mkdir(exist_ok=True)
    env = {"HOME": str(home), "PATH": "/usr/bin:/bin",
           "http_proxy": "http://127.0.0.1:9", "https_proxy": "http://127.0.0.1:9",
           "HTTP_PROXY": "http://127.0.0.1:9", "HTTPS_PROXY": "http://127.0.0.1:9"}
    started = time.monotonic()
    proc = subprocess.run([str(binary), "scan", "-path", str(project),
                           "-lockfile", str(scratch / "locks" / (name + ".json")), "-json"],
                          capture_output=True, env=env, timeout=60, check=False)
    duration = round((time.monotonic() - started) * 1000, 2)
    try:
        payload = json.loads(proc.stdout.decode())
    except (ValueError, UnicodeError):
        return {"error": f"exit={proc.returncode} stdout unreadable", "stderr": proc.stderr.decode()[:300],
                "alert": False, "rules": [], "duration_ms": duration}
    findings = []
    for artifact in payload.get("artifacts") or []:
        findings.extend(artifact.get("findings") or [])
    return {"alert": bool(findings), "rules": sorted({f.get("ruleId", "?") for f in findings}),
            "exit": proc.returncode, "duration_ms": duration}


def confusion(samples: list[dict]) -> dict:
    tp = sum(1 for s in samples if s["label"] == "malicious" and s["result"]["alert"])
    fn = sum(1 for s in samples if s["label"] == "malicious" and not s["result"]["alert"])
    fp = sum(1 for s in samples if s["label"] == "benign" and s["result"]["alert"])
    tn = sum(1 for s in samples if s["label"] == "benign" and not s["result"]["alert"])
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    return {"samples": len(samples), "malicious": tp + fn, "benign": fp + tn,
            "TP": tp, "FP": fp, "TN": tn, "FN": fn,
            "precision": round(precision, 3) if precision is not None else None,
            "recall": round(recall, 3) if recall is not None else None,
            "misses": [s["name"] for s in samples if s["label"] == "malicious" and not s["result"]["alert"]],
            "false_alarms": [s["name"] for s in samples if s["label"] == "benign" and s["result"]["alert"]],
            "informational_only": [s["name"] for s in samples
                                   if not s["result"]["alert"] and s["result"]["informational_rules"]]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", default=str(ROOT / "tests/fixtures/local_scan_corpus"))
    parser.add_argument("--bundled", action="store_true", help="also scan the pinned bundled skill sources")
    parser.add_argument("--eyebrow-bin", default="", help="pinned upstream eyebrow binary for comparison")
    parser.add_argument("--scratch", default="", help="scratch dir for upstream layout (SSD)")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    corpus = pathlib.Path(args.corpus)
    manifest = json.loads((corpus / "manifest.json").read_text())
    scratch = pathlib.Path(args.scratch or tempfile.mkdtemp(prefix="addon-local-scan-eval-"))
    scratch.mkdir(parents=True, exist_ok=True)
    (scratch / "locks").mkdir(exist_ok=True)  # the binary writes its lockfile beside a temp file here
    eyebrow = pathlib.Path(args.eyebrow_bin) if args.eyebrow_bin else None
    output = {"schema": 1, "alert_threshold": "confidence in {medium, high}",
              "groups": {}, "samples": {}}

    for group in ("tuning", "tuning-extension", "holdout-v2"):
        rows = []
        for path in sorted((corpus / group).glob("*.md")):
            content = path.read_bytes()
            recorded = manifest["samples"].get(f"{group}/{path.name}", {})
            import hashlib
            digest = hashlib.sha256(content).hexdigest()
            if recorded.get("sha256") not in (None, digest):
                raise SystemExit(f"corpus hash drift for {group}/{path.name}; re-freeze deliberately")
            entry = {"name": f"{group}/{path.name}", "label": recorded.get("label"),
                     "result": local_result(content)}
            if eyebrow:
                entry["eyebrow"] = eyebrow_result(eyebrow, scratch, path.stem, content)
            rows.append(entry)
            output["samples"][entry["name"]] = entry
        output["groups"][group] = confusion(rows)
        if eyebrow:
            agreement = sum(1 for r in rows if r["result"]["alert"] == r["eyebrow"]["alert"])
            output["groups"][group]["eyebrow_agreement"] = f"{agreement}/{len(rows)}"
            output["groups"][group]["eyebrow_only_catches"] = [r["name"] for r in rows
                                                               if r["eyebrow"]["alert"] and not r["result"]["alert"]]
            output["groups"][group]["local_only_catches"] = [r["name"] for r in rows
                                                             if r["result"]["alert"] and not r["eyebrow"]["alert"]]

    if args.bundled:
        from core.addon_packages import _files
        rows = []
        for digest, text in _files().items():
            content = text.encode("utf-8")
            entry = {"name": digest[:16], "label": "catalogue (expected benign, curated)",
                     "result": local_result(content)}
            if eyebrow:
                entry["eyebrow"] = eyebrow_result(eyebrow, scratch, "bundled-" + digest[:12], content)
            rows.append(entry)
        output["groups"]["bundled_catalogue"] = {
            "samples": len(rows),
            "local_alerts": [r["name"] for r in rows if r["result"]["alert"]],
            "local_informational": [r["name"] for r in rows if r["result"]["informational_rules"]],
            "eyebrow_alerts": [r["name"] for r in rows if r.get("eyebrow", {}).get("alert")],
            "mean_local_duration_ms": round(sum(r["result"]["duration_ms"] for r in rows) / len(rows), 3),
        }
        output["samples"].update({f"bundled/{r['name']}": r for r in rows})

    pathlib.Path(args.out).write_text(json.dumps(output, indent=2) + "\n")
    for group, metrics in output["groups"].items():
        print(group, json.dumps({k: v for k, v in metrics.items() if not isinstance(v, list)}))
        for key in ("misses", "false_alarms", "local_alerts"):
            if metrics.get(key):
                print(" ", key, metrics[key])
    print("wrote", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
