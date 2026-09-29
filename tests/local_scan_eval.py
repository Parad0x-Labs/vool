"""Comparative evaluation runner for the local skill scanner.

Runs two scanner builds (typically the unchanged baseline and the candidate)
over identical bytes with one alert threshold, and reports raw counts:
alerts, false alarms, misses, stance-correct documentation handling and
runtime. It makes no accuracy claim and never talks to the network.

Usage:
  python tests/local_scan_eval.py --baseline SYS_PATH --candidate SYS_PATH \
      --challenge DIR [--catalogue] --out REPORT.json

The challenge directory must contain labels.json (id/file/label/expected) and
the sample files; inputs are hashed before scanning and the report records
those hashes so the evaluation is bound to exact bytes.
"""
import argparse
import hashlib
import importlib.util
import json
import statistics
import sys
import time
from pathlib import Path

ALERT_CONFIDENCE = {"medium", "high"}
MALICIOUS, BENIGN, AMBIGUOUS = "malicious", "benign", "ambiguous"


def load_module(path: str):
    spec = importlib.util.spec_from_file_location("scan_build_%s" % abs(hash(path)), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def evaluate(report: dict, label: str, expected: str) -> dict:
    findings = report.get("findings") or []
    alert_findings = [f for f in findings if f.get("confidence") in ALERT_CONFIDENCE]
    alert = bool(alert_findings) or report.get("status") == "incomplete"
    overclaim = any(f.get("severity") == "high" and f.get("confidence") in ALERT_CONFIDENCE for f in alert_findings)
    row = {"alert": alert, "status": report.get("status"),
           "alert_rules": sorted({f["ruleId"] for f in alert_findings}),
           "informational_rules": sorted({f["ruleId"] for f in findings if f not in alert_findings}),
           "finding_count": len(findings)}
    if label == MALICIOUS:
        row["outcome"] = "TP" if alert else "FN"
    elif label == BENIGN:
        row["outcome"] = "FP" if alert else "TN"
    else:  # ambiguous: binary ground truth is not honest; judge over-claiming
        row["outcome"] = "over-claim" if overclaim else ("calm-alert" if alert else "quiet")
        row["meets_uncertain_contract"] = not overclaim
    row["expected_match"] = (
        row["outcome"] in {"TP", "TN"} if expected in {"alert", "no-alert"}
        else row["meets_uncertain_contract"])
    return row


def scan_challenge(module, challenge_dir: Path) -> dict:
    labels = json.loads((challenge_dir / "labels.json").read_text())
    samples = []
    for entry in labels["samples"]:
        data = (challenge_dir / entry["file"]).read_bytes()
        samples.append({"id": entry["id"], "label": entry["label"], "expected": entry["expected"],
                        "sha256": hashlib.sha256(data).hexdigest(), "bytes": data})
    results = []
    for sample in samples:
        started = time.perf_counter()
        report = module.scan_bytes(sample["id"], sample["bytes"])
        duration_ms = (time.perf_counter() - started) * 1000
        row = evaluate(report, sample["label"], sample["expected"])
        row.update({"id": sample["id"], "duration_ms": round(duration_ms, 3)})
        if report.get("findings"):
            row["stances"] = sorted({f.get("stance", "") for f in report["findings"]})
        results.append(row)
    return _summarise(results, samples)


def scan_original_corpus(module, corpus_dir: Path) -> dict:
    """The first mission's labeled corpus (tests/fixtures/local_scan_corpus):
    manifest.json maps each sample path to its label and sha256. Hashes are
    verified before scanning so results bind to the exact frozen bytes."""
    manifest = json.loads((corpus_dir / "manifest.json").read_text())
    results = []
    samples = []
    for path, meta in sorted(manifest["samples"].items()):
        data = (corpus_dir / path).read_bytes()
        actual = hashlib.sha256(data).hexdigest()
        if actual != meta["sha256"]:
            raise SystemExit("corpus sample hash mismatch: %s" % path)
        samples.append({"id": path, "label": meta["label"], "bytes": data})
    for sample in samples:
        started = time.perf_counter()
        report = module.scan_bytes(sample["id"], sample["bytes"])
        duration_ms = (time.perf_counter() - started) * 1000
        # The original corpus is binary-labelled (benign/malicious); there is
        # no ambiguous class, so expected behaviour follows the label.
        expected = "alert" if sample["label"] == MALICIOUS else "no-alert"
        row = evaluate(report, sample["label"], expected)
        row.update({"id": sample["id"], "duration_ms": round(duration_ms, 3)})
        if report.get("findings"):
            row["stances"] = sorted({f.get("stance", "") for f in report["findings"]})
            row["rules"] = sorted({f["ruleId"] for f in report["findings"]})
        results.append(row)
    return _summarise(results, samples)


def scan_catalogue(module) -> dict:
    repo_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(repo_root))
    from core import addon_packages, addon_store
    entries = [e for e in addon_store.CATALOG if e.get("bundled_source")]
    results = []
    for entry in entries:
        data = addon_packages.source(entry["sha256"])
        started = time.perf_counter()
        report = module.scan_bytes(entry["skill_name"], data)
        duration_ms = (time.perf_counter() - started) * 1000
        alert_findings = [f for f in report.get("findings") or [] if f.get("confidence") in ALERT_CONFIDENCE]
        results.append({"id": entry["id"], "sha256": entry["sha256"][:16],
                        "alert": bool(alert_findings) or report.get("status") == "incomplete",
                        "alert_rules": sorted({f["ruleId"] for f in alert_findings}),
                        "informational_count": len(report.get("findings") or []) - len(alert_findings),
                        "stances": sorted({f.get("stance", "") for f in report.get("findings") or []}),
                        "duration_ms": round(duration_ms, 3)})
    return {"samples": len(results), "alerts": [r["id"] for r in results if r["alert"]],
            "alert_count": sum(1 for r in results if r["alert"]),
            "informational_only_count": sum(1 for r in results if not r["alert"] and r["informational_count"]),
            "mean_duration_ms": round(statistics.fmean(r["duration_ms"] for r in results), 3),
            "max_duration_ms": round(max(r["duration_ms"] for r in results), 3),
            "rows": results}


def _summarise(results, samples):
    labelled = [r for r in results if r["outcome"] in {"TP", "FP", "TN", "FN"}]
    group = {"samples": len(samples),
             "TP": sum(1 for r in labelled if r["outcome"] == "TP"),
             "FP": sum(1 for r in labelled if r["outcome"] == "FP"),
             "TN": sum(1 for r in labelled if r["outcome"] == "TN"),
             "FN": sum(1 for r in labelled if r["outcome"] == "FN"),
             "misses": [r["id"] for r in labelled if r["outcome"] == "FN"],
             "false_alarms": [r["id"] for r in labelled if r["outcome"] == "FP"],
             "ambiguous": [r["id"] for r in results if r["outcome"] not in {"TP", "FP", "TN", "FN"}],
             "over_claims": [r["id"] for r in results if r.get("outcome") == "over-claim"],
             "incomplete": [r["id"] for r in results if r.get("status") == "incomplete"],
             "mean_duration_ms": round(statistics.fmean(r["duration_ms"] for r in results), 3),
             "max_duration_ms": round(max(r["duration_ms"] for r in results), 3),
             "rows": results}
    pos = group["TP"] + group["FN"]
    neg = group["TN"] + group["FP"]
    group["precision"] = round(group["TP"] / (group["TP"] + group["FP"]), 3) if group["TP"] + group["FP"] else None
    group["recall"] = round(group["TP"] / pos, 3) if pos else None
    group["negative_predictive"] = round(group["TN"] / neg, 3) if neg else None
    return group


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", required=True, help="path to baseline core/local_scan.py")
    parser.add_argument("--candidate", required=True, help="path to candidate core/local_scan.py")
    parser.add_argument("--challenge", help="directory with labels.json + samples/")
    parser.add_argument("--catalogue", action="store_true", help="also scan bundled catalogue skills")
    parser.add_argument("--corpus", help="original labeled corpus directory (manifest.json format)")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    challenge_dir = Path(args.challenge) if args.challenge else None
    frozen = {}
    if challenge_dir is not None:
        for line in (challenge_dir / "SHA256SUMS").read_text().splitlines():
            digest, _, name = line.partition("  ")
            frozen[name.strip()] = digest
        for name, digest in frozen.items():
            data = (challenge_dir / name).read_bytes()
            actual = hashlib.sha256(data).hexdigest()
            if actual != digest:
                raise SystemExit("frozen input changed: %s" % name)

    builds = {"baseline": load_module(args.baseline), "candidate": load_module(args.candidate)}
    report = {"schema": 1,
              "alert_threshold": "any finding with confidence in {medium, high}; incomplete coverage counts as not-quiet",
              "frozen_sha256sums": frozen,
              "challenge": {}, "catalogue": {}, "corpus": {}}
    for name, module in builds.items():
        if challenge_dir is not None:
            report["challenge"][name] = scan_challenge(module, challenge_dir)
        if args.catalogue:
            report["catalogue"][name] = scan_catalogue(module)
        if args.corpus:
            report["corpus"][name] = scan_original_corpus(module, Path(args.corpus))
    Path(args.out).write_text(json.dumps(report, indent=1, sort_keys=True))
    print("wrote %s" % args.out)
    for name in builds:
        if challenge_dir is not None:
            group = report["challenge"][name]
            print("%s challenge: TP %s FP %s TN %s FN %s over-claims %s incomplete %s mean %.3f ms" %
                  (name, group["TP"], group["FP"], group["TN"], group["FN"],
                   len(group["over_claims"]), len(group["incomplete"]), group["mean_duration_ms"]))
        if args.corpus:
            group = report["corpus"][name]
            print("%s corpus: TP %s FP %s TN %s FN %s incomplete %s mean %.3f ms" %
                  (name, group["TP"], group["FP"], group["TN"], group["FN"],
                   len(group["incomplete"]), group["mean_duration_ms"]))


if __name__ == "__main__":
    main()
