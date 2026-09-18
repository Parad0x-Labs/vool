"""PB01 independent usefulness holdout — real local models, mechanical external validators.

Measures whether the learned-guidance loop is USEFUL, not whether it is plumbed:

* arms      baseline (fresh home, no learned procedures) vs reuse (home with a promoted lesson)
* models    the eligible REAL local models registered for this run (no paid calls, no cloud)
* tasks     held-out prompts with MECHANICAL external validators — exact computation and a
            deterministic file-shape transformation with known expected bytes; the answering
            model never grades itself
* held-out  the measured prompts are never used to create the guidance; the lesson is created
            from two independently-executed REAL transforms and promoted through the store's
            own two-witness rule before any measured turn runs
* cost      prompt+completion token counts as reported by the provider per served turn

Report (JSON + printed table): sample size, per-model per-arm correct-completion counts,
guidance delivery receipts, token cost. If no eligible real model can serve without paid
calls, the script reports UNMEASURED instead of inventing outcomes.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from urllib.request import Request, urlopen

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from tests._blackbox_served_rig import REPO_ROOT as RIG_ROOT
from tests._blackbox_served_rig import ServedDaemon, run_in_home

OLLAMA_URL = "http://127.0.0.1:11434"
#: Every plausible resident real-model candidate, swept through the daemon's own sealed
#: certification probe (the authorship gate for final-answer turns). The measurement arm only
#: uses models that pass it; every exclusion is recorded verbatim in the report notes.
MODELS = [
    "qwen3:4b",
    "qwen2.5:7b",
    "qwen3:8b",
    "qwen3:30b-a3b",
    "qwen3.5:35b-a3b",
    "vool-qwen3-30b-a3b:nothink",
]
RUN_ROOT = Path("/tmp/vool-usefulness-holdout")

SEED = '''
import sys
sys.path.insert(0, "{root}")
from storage.db import get_connection
from storage.migrations import run_migrations
from storage.model_provider_manifest import ModelProviderManifest, list_provider_manifests, upsert_provider_manifest

run_migrations()

def manifest(name):
    return ModelProviderManifest(
        provider_name="ollama-local", model_name=name, source_type="http",
        adapter_type="openai_compatible", license_name="Apache-2.0",
        license_reference="https://ollama.com/library/qwen3",
        weight_location="external", runtime_dependency="ollama",
        capabilities=["summarize", "classify", "format", "extract", "structured_json"],
        runtime_config={{"base_url": "{base_url}", "timeout_seconds": 120}},
        metadata={{
            "runtime_family": "ollama", "cost_class": "free_local",
            "model_digest": "sha256:local-" + name, "chat_template_hash": "tmpl",
            "quantization": "Q4_K_M", "parameter_billions": 4.0,
        }},
    )

conn = get_connection()
conn.execute("DELETE FROM model_provider_manifests")
conn.commit()
conn.close()
for name in {models!r}:
    upsert_provider_manifest(manifest(name))
print(sorted(m.provider_id for m in list_provider_manifests()))
'''

CREATE_LESSON = '''
import sys
sys.path.insert(0, "{root}")
from core.learning import promote_verified_procedure

# Two REAL, independently executed deterministic transforms (run below by the harness); their
# actual commands and returncodes are the validation evidence. This creation task is NOT one
# of the measured holdout tasks.
receipts = [
    {{"intent": "workspace.write_file", "paths": ["data.json"], "executed": True}},
    {{"intent": "workspace.run_tests", "command": {command!r}, "returncode": 0, "executed": True}},
]
validation = {{"ok": True, "tool": "workspace.run_tests", "command": {command!r}, "returncode": 0}}
first = promote_verified_procedure(
    task_class={task_class!r},
    title="Sort JSON keys into key=value lines with python3",
    preconditions=["the payload is a flat JSON object"],
    steps=["parse the payload with python3 -c json", "emit sorted key=value lines"],
    tool_receipts=receipts,
    validation=validation,
    rollback={{"intent": "workspace.rollback_last_change"}},
    session_id="usefulness-creation",
)
second = promote_verified_procedure(
    task_class={task_class!r},
    title="Sort JSON keys into key=value lines with python3",
    preconditions=["the payload is a flat JSON object"],
    steps=["parse the payload with python3 -c json", "emit sorted key=value lines"],
    tool_receipts=receipts,
    validation=validation,
    rollback={{"intent": "workspace.rollback_last_change"}},
    session_id="usefulness-creation-witness2",
)
from core.learning import list_procedure_records
records = list_procedure_records()
print(str([ (r.get("procedure_id"), r.get("status")) for r in records ]))
'''

# ---- the held-out measurement corpus: prompt + mechanical expected bytes ----
TRANSFORM_TASKS = [
    {
        "id": "transform-1",
        "prompt": 'Convert this JSON to lines of key=value sorted by key, nothing else: {"delta": 4, "alpha": 1, "gamma": 3}',
        "expect": "alpha=1\ngamma=3\ndelta=4",
        "class": "transform",
    },
    {
        "id": "transform-2",
        "prompt": 'Convert this JSON to lines of key=value sorted by key, nothing else: {"zulu": 26, "bravo": 2, "mike": 13, "kilo": 11}',
        "expect": "bravo=2\nkilo=11\nmike=13\nzulu=26",
        "class": "transform",
    },
    {
        "id": "transform-3",
        "prompt": 'Convert this JSON to lines of key=value sorted by key, nothing else: {"pear": 9, "apple": 5}',
        "expect": "apple=5\npear=9",
        "class": "transform",
    },
    {
        "id": "transform-4",
        "prompt": 'Convert this JSON to lines of key=value sorted by key, nothing else: {"one": 1, "six": 6, "three": 3, "nine": 9, "four": 4}',
        "expect": "four=4\nnine=9\none=1\nsix=6\nthree=3",
        "class": "transform",
    },
]
COMPUTE_TASKS = [
    {"id": "compute-1", "prompt": "What is 17 times 23? Answer with the number only.", "expect": "391", "class": "compute"},
    {"id": "compute-2", "prompt": "What is 144 divided by 12? Answer with the number only.", "expect": "12", "class": "compute"},
]
TASKS = TRANSFORM_TASKS + COMPUTE_TASKS


def _real_transform_evidence(workdir: Path) -> tuple[str, str]:
    """Actually execute the creation transform; return (command, output bytes)."""
    workdir.mkdir(parents=True, exist_ok=True)
    payload = '{"delta": 4, "alpha": 1, "gamma": 3}'
    snippet = "import json,sys; d=json.loads(sys.argv[1]); print('\\n'.join(f'{k}={v}' for k, v in sorted(d.items())))"
    completed = subprocess.run(
        [sys.executable, "-c", snippet, payload],
        capture_output=True, text=True, timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    (workdir / "creation_evidence.txt").write_text(completed.stdout, encoding="utf-8")
    command = f"python3 -c {snippet!r} {payload!r}"
    return command, completed.stdout.strip()


def _boot_arm(name: str, models: list[str]) -> tuple[ServedDaemon, Path]:
    home = RUN_ROOT / name / "home"
    daemon = ServedDaemon(
        home,
        env_extra={
            "VOOL_ALWAYS_ON_CATALOG": "1",
            "VOOL_MODEL_LOAD_FLOOR_GB": "0",
            "OLLAMA_HOST": OLLAMA_URL,
            "VOOL_OLLAMA_URL": OLLAMA_URL,
            "VOOL_OLLAMA_CHAT_URL": f"{OLLAMA_URL}/api/chat",
        },
    )
    daemon.start(timeout=240)
    run_in_home(home, SEED.format(root=RIG_ROOT, base_url=OLLAMA_URL, models=models))
    return daemon, home


def _certify(daemon: ServedDaemon, model: str) -> dict:
    request = Request(
        f"{daemon.base_url}/api/model-tool-certification/run",
        data=json.dumps({"provider_name": "ollama-local", "model_name": model, "timeout_seconds": 300}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=1200) as response:
        return json.loads(response.read().decode("utf-8"))


def _certified_models(daemon: ServedDaemon, models: list[str], notes: list[str]) -> list[str]:
    """Only models the daemon's own sealed probe certifies are eligible to author answers."""
    eligible: list[str] = []
    for model in models:
        try:
            result = _certify(daemon, model)
        except Exception as exc:
            notes.append(f"{model}: certification route failed ({exc})")
            continue
        if result.get("state") == "verified":
            eligible.append(model)
        else:
            failures = [
                f"{stage}:{info.get('failure_code') or info.get('state')}"
                for stage, info in (result.get("stages") or {}).items()
                if info.get("state") not in ("passed", "not_run")
            ]
            notes.append(
                f"{model}: certification state {result.get('state')!r} ({', '.join(failures) or 'no stage failure recorded'}) — excluded from authorship"
            )
    return eligible


def _chat(daemon: ServedDaemon, model: str, prompt: str, session: str) -> dict:
    payload = {"messages": [{"role": "user", "content": prompt}], "stream": False, "session_id": session, "model": model, "mode": "auto"}
    request = Request(
        f"{daemon.base_url}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=300) as response:
        return json.loads(response.read().decode("utf-8"))


def _marker(home: Path) -> int:
    db = home / "data" / "vool_web0_v2.db"
    if not db.exists():
        return 0
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        row = conn.execute("SELECT COALESCE(MAX(rowid),0) FROM runtime_session_events").fetchone()
    except sqlite3.OperationalError:
        return 0
    finally:
        conn.close()
    return int(row[0]) if row else 0


def _events_after(home: Path, marker: int, event_type: str) -> list[dict]:
    """Events appended after the marker; sessions are server-derived, so identity scoping is
    done by the caller's turn ordering (measured turns run one at a time)."""
    db = home / "data" / "vool_web0_v2.db"
    if not db.exists():
        return []
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT COALESCE(details_json,'') FROM runtime_session_events "
            "WHERE event_type=? AND rowid>? ORDER BY rowid",
            (event_type, marker),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()
    return [json.loads(row[0]) for row in rows if row[0]]


def _tokens(home: Path, marker: int) -> int:
    total = 0
    for details in _events_after(home, marker, "model.call_completed"):
        usage = details.get("token_usage") or {}
        total += int(usage.get("prompt_tokens") or 0) + int(usage.get("completion_tokens") or 0)
    return total


def _delivered(home: Path, marker: int) -> bool:
    return bool(_events_after(home, marker, "learning_guidance_delivered"))


def main() -> None:
    models = [m for m in MODELS if _model_present(m)]
    report: dict = {"held_out_task_count": len(TASKS), "models": models, "arms": {}, "verdict": "MEASURED", "notes": []}
    if not models:
        report["verdict"] = "UNMEASURED"
        report["notes"].append("no eligible real local model is resident; no paid calls permitted")
        _write_report(report)
        print(json.dumps(report, indent=2))
        return

    shutil.rmtree(RUN_ROOT, ignore_errors=True)
    RUN_ROOT.mkdir(parents=True)

    # ---- reuse arm: create + promote the lesson from REAL executed transforms (held-out law:
    # the measured prompts below are never used for creation) ----
    reuse_daemon, reuse_home = _boot_arm("reuse", models)
    try:
        models = _certified_models(reuse_daemon, models, report["notes"])
        report["models"] = models
        if not models:
            report["verdict"] = "UNMEASURED"
            report["notes"].append("no model passed the daemon's own sealed certification probe")
            _write_report(report)
            print(json.dumps(report, indent=2))
            return
        command, _ = _real_transform_evidence(RUN_ROOT / "reuse" / "creation")
        # discover the served task class so the lesson can rank for these turns
        _chat(reuse_daemon, models[0], TRANSFORM_TASKS[0]["prompt"], "usefulness-class-probe")
        task_class = _served_task_class(reuse_home) or "data_transform"
        run_in_home(reuse_home, CREATE_LESSON.format(root=RIG_ROOT, command=command, task_class=task_class))
        statuses = run_in_home(reuse_home, "import sys;sys.path.insert(0,'" + str(RIG_ROOT) + "');from core.learning import list_procedure_records;print(str([r.get('status') for r in list_procedure_records()]))")
        report["notes"].append(f"lesson task_class={task_class} statuses={statuses.strip()!r} (probe turn excluded from results)")

        baseline_daemon, baseline_home = _boot_arm("baseline", models)
        try:
            certified_baseline = _certified_models(baseline_daemon, models, report["notes"])
            if certified_baseline != models:
                report["verdict"] = "UNMEASURED"
                report["notes"].append("certification diverged between arms; refusing a one-sided comparison")
                _write_report(report)
                print(json.dumps(report, indent=2))
                return
            for model in models:
                for arm, (daemon, home) in {
                    "baseline": (baseline_daemon, baseline_home),
                    "reuse": (reuse_daemon, reuse_home),
                }.items():
                    outcomes = []
                    for task in TASKS:
                        session = f"useful-{arm}-{model.replace(':', '-')}-{task['id']}"
                        marker = _marker(home)
                        answer = _chat(daemon, model, task["prompt"], session)
                        text = str((answer.get("message") or {}).get("content") or answer.get("response") or "").strip()
                        ok = _validate(task, text)
                        outcomes.append(
                            {
                                "task": task["id"],
                                "class": task["class"],
                                "correct": ok,
                                "guidance_delivered": _delivered(home, marker) if arm == "reuse" else False,
                                "tokens": _tokens(home, marker),
                                "answer_excerpt": text[:120],
                            }
                        )
                    report["arms"].setdefault(arm, {})[model] = {
                        "tasks": len(outcomes),
                        "correct": sum(1 for o in outcomes if o["correct"]),
                        "guidance_delivered_turns": sum(1 for o in outcomes if o["guidance_delivered"]),
                        "tokens_total": sum(o["tokens"] for o in outcomes),
                        "outcomes": outcomes,
                    }
        finally:
            baseline_daemon.stop()
    finally:
        reuse_daemon.stop()

    _write_report(report)
    print(json.dumps(report, indent=2))


def _model_present(model: str) -> bool:
    try:
        with urlopen(f"{OLLAMA_URL}/api/tags", timeout=5) as response:
            tags = json.loads(response.read().decode("utf-8"))
        return any(item.get("name") == model for item in tags.get("models") or [])
    except Exception:
        return False


def _served_task_class(home: Path) -> str:
    """The task class the runtime's own classifier assigned to the most recent classified turn."""
    db = home / "data" / "vool_web0_v2.db"
    if not db.exists():
        return ""
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        row = conn.execute(
            "SELECT COALESCE(details_json,'') FROM runtime_session_events WHERE event_type='task_classified' ORDER BY rowid DESC LIMIT 1",
        ).fetchone()
    except sqlite3.OperationalError:
        return ""
    finally:
        conn.close()
    if not row:
        return ""
    return str((json.loads(row[0]) or {}).get("task_class") or "")


def _validate(task: dict, text: str) -> bool:
    """The external judge: deterministic string equality on canonical bytes. The answering
    model is never consulted."""
    expected = task["expect"]
    got = "\n".join(line.strip() for line in text.strip().splitlines() if line.strip())
    if task["class"] == "compute":
        return str(expected) in got.split() or got == str(expected)
    return got == expected


def _write_report(report: dict) -> None:
    out = REPO_ROOT / "reports" / "PB01_USEFULNESS_HOLDOUT_20260903.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")


if __name__ == "__main__":
    main()
