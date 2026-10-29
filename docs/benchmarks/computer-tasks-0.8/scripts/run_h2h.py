#!/usr/bin/env python3
"""Computer-task head-to-head: VOOL vs OpenClaw vs Hermes on the same asks, same local model (qwen3:8b on the
standalone Ollama at 127.0.0.1:11434), same fake home, same scorer.

usage: python3 -I run_h2h.py --system vool|openclaw|hermes --set fset|hset|fresh [--ids F06,F14] [--tag NAME]

- Asks: fset/hset = the 30 reply_must asks of audit-files-20261027/cases.jsonl / router-holdout H01-H30;
  fresh = fresh-set/fresh-asks.json (independent author, frozen).
- Every ask gets its own APFS clone of the fake home (without Library/Caches/com.apple.python) and, for the rivals,
  its own state dir; rivals run under sandbox-exec (rival.sb: writes only to the clone, the state dir and tmp;
  network only to localhost). A multi-turn ask stays in one session.
- VOOL runs through the frozen audit harness (harness sha e9244383) on the fix14 tree, one daemon per run, its own
  clone of the fake home.
- Scoring: fset/hset with live_door.score (reply_must + unsure rule, unchanged); fresh with must_include (exact,
  case-insensitive, any_of allowed) and must_not_include; confident-wrong = a miss that does not say it could not find.
- Writes runs/<tag>/<system>-<set>.jsonl (one row per ask: replies, wall_s, tokens if the tool reports them, score,
  clone md5 before/after of the owner files) and prints a summary. Resumable: ids already written are skipped.
"""
import argparse, hashlib, json, os, re, shutil, subprocess, sys, time
from pathlib import Path

W = Path("<work>")
M = W / "missions/bench-win-20261028/h2h-computer"
FAKEHOME = W / "missions/audit-files-20261027/fakehome"
OC = W / "cache/memory-h2h-20261005/mr29-eval/openclaw/node_modules/.bin/openclaw"
NODE_BIN = W / "cache/memory-h2h-20261005/mr29-eval/node26/bin"
HERMES = W / "cache/memory-h2h-20261005/hermes-agent"
VOOL_REPO = W / "missions/clean-loop-speed-20261028/remeasure/fix14-repo"
VOOL_PY = W / "missions/drop-x-port-076-20261027/venv/bin/python"
HARNESS = W / "missions/clean-loop-audit-20261028/harness/audit_ask.py"
MODEL = "qwen3:8b"
CLOUD = {"on": False, "proxy": "http://127.0.0.1:18765/v1", "model": "z-ai/glm-5.3-flashx"}
UNSURE = re.compile(r"\b(couldn'?t find|can'?t find|could not find|not sure|which one|could you|do you mean|no (?:file|folder)|"
                    r"don'?t see|do not see|cannot|can'?t|not there|doesn'?t exist|does not exist|no such|isn'?t there|"
                    r"wasn'?t able|unable)\b")


def load_set(name):
    if name == "fset":
        cases = [json.loads(l) for l in open(W / "missions/audit-files-20261027/cases.jsonl")]
        return [{"id": c["id"], "turns": c["turns"], "reply_must": c["expect"]["reply_must"]} for c in cases if c["expect"].get("reply_must")]
    if name == "hset":
        cases = [json.loads(l) for l in open(W / "missions/router-holdout-20261028/payload/holdout-cases.jsonl")]
        return [{"id": c["id"], "turns": c["turns"], "reply_must": c["expect"]["reply_must"]} for c in cases]
    src = "round2/round2-asks.json" if name == "r2" else "fresh-set/fresh-asks.json"  # r2: round 2, new home + new asks
    asks = json.load(open(W / "missions/bench-win-20261028" / src))
    return [{"id": a["id"], "turns": a["turns"], "must_include": a["must_include"], "must_not_include": a.get("must_not_include", []),
             "category": a["category"]} for a in asks]


def score(ask, replies):
    text = " ".join(replies).lower()
    unsure = bool(UNSURE.search(text))
    if "reply_must" in ask:
        ok = all(m.lower() in text for m in ask["reply_must"])
        return {"ok": ok, "conf_wrong": (not ok) and not unsure}
    hits = []
    for m in ask["must_include"]:
        alts = m["any_of"] if isinstance(m, dict) else [m]
        hits.append(any(str(a).lower() in text for a in alts))
    bad = [x for x in ask["must_not_include"] if str(x).lower() in text]
    ok = all(hits) and not bad
    return {"ok": ok, "conf_wrong": (not ok) and (bool(bad) or not unsure), "missing": [m for m, h in zip(ask["must_include"], hits) if not h], "forbidden_hit": bad}


def owner_files():
    out = []
    for r, ds, fs in os.walk(FAKEHOME):
        ds[:] = sorted(d for d in ds if not (Path(r) == FAKEHOME and d == "Library") and not d.startswith("."))
        out += [str((Path(r) / f).relative_to(FAKEHOME)) for f in sorted(fs)]
    return out


def owner_md5(root):
    """md5 over the fixture's own files only (owner files): a changed, moved or deleted owner file changes it. Files a
    tool adds for itself (OpenClaw's workspace bootstrap, .git) do not; they are listed by added_files()."""
    h = hashlib.md5()
    for rel in owner_files():
        p = Path(root) / rel
        h.update(rel.encode()); h.update(p.read_bytes() if p.exists() else b"<missing>")
    return h.hexdigest()


def added_files(root):
    known = set(owner_files())
    extra = []
    for r, ds, fs in os.walk(root):
        ds[:] = [d for d in ds if not (Path(r) == Path(root) and d == "Library")]
        extra += [str((Path(r) / f).relative_to(root)) for f in fs if str((Path(r) / f).relative_to(root)) not in known]
    return sorted(extra)


def clone_home(dst):
    if dst.exists():
        shutil.rmtree(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["cp", "-c", "-R", str(FAKEHOME), str(dst)], check=True)
    shutil.rmtree(dst / "Library/Caches/com.apple.python", ignore_errors=True)


CAP_S = 300  # per-ask wall cap for every system (coordinator ruling); a timeout counts as no answer


class Timed:
    def __init__(self, rc, out, err):
        self.returncode, self.stdout, self.stderr = rc, out, err


def sandboxed(home, state, argv, env, timeout):
    """Run in the rival sandbox in its own process group; on timeout stop only that group (we started it)."""
    import signal
    cmd = ["sandbox-exec", "-f", str(M / "rival.sb"), "-D", f"HOME_CLONE={home}", "-D", f"STATE_DIR={state}",
           "/usr/bin/env", "-i", *[f"{k}={v}" for k, v in env.items()], *argv]
    t = time.time()
    proc = subprocess.Popen(cmd, cwd=home, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
    try:
        out, err = proc.communicate(timeout=max(1, timeout))
        return Timed(proc.returncode, out, err), time.time() - t
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            out, err = proc.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL); out, err = proc.communicate()
        return Timed("timeout", out or "", (err or "") + "\n[h2h: per-ask cap reached]"), time.time() - t


def run_openclaw(ask, home, state):
    state.mkdir(parents=True, exist_ok=True)
    if CLOUD["on"]:
        prov = {"bench": {"baseUrl": CLOUD["proxy"], "apiKey": "bench-loopback-only", "api": "openai-completions",
                "models": [{"id": CLOUD["model"], "name": CLOUD["model"], "contextTokens": 200000, "maxTokens": 4096}]}}
        mref = "bench/" + CLOUD["model"]
    else:
        prov = {"ollama": {"baseUrl": "http://127.0.0.1:11434", "apiKey": "ollama-local", "api": "ollama",
                "models": [{"id": MODEL, "name": MODEL, "contextTokens": 40960, "maxTokens": 4096}]}}
        mref = "ollama/" + MODEL
    cfg = {"models": {"providers": prov},
           "agents": {"defaults": {"workspace": str(home), "model": mref}},
           "logging": {"file": str(state / "openclaw.log"), "consoleLevel": "error"},
           "update": {"checkOnStart": False}, "diagnostics": {"enabled": False}}
    (state / "openclaw.json").write_text(json.dumps(cfg, indent=1))
    env = {"HOME": str(home), "OPENCLAW_STATE_DIR": str(state), "OPENCLAW_CONFIG_PATH": str(state / "openclaw.json"),
           "TMPDIR": "/private/tmp", "OPENCLAW_NO_RESPAWN": "1", "PATH": f"{NODE_BIN}:/usr/bin:/bin"}
    replies, wall, tokens, errs = [], 0.0, 0, []
    for turn in ask["turns"]:
        p, s = sandboxed(home, state, [str(OC), "agent", "--local", "--json", "--session-id", "h2h-" + ask["id"], "--timeout", "600", "-m", turn], env, CAP_S - wall)
        wall += s
        try:
            j = json.loads(p.stdout[p.stdout.find("{"):])
            replies.append(" ".join(x.get("text") or "" for x in j.get("payloads", [])))
            tokens += int(((j.get("meta") or {}).get("agentMeta") or {}).get("usage", {}).get("total") or 0)
        except Exception:
            replies.append(""); errs.append(f"rc={p.returncode} {p.stderr[-300:]}")
        if p.returncode == "timeout":
            break
    return replies, wall, tokens, errs


def run_hermes(ask, home, state):
    state.mkdir(parents=True, exist_ok=True)
    if CLOUD["on"]:
        base, mdl = CLOUD["proxy"], CLOUD["model"]
        (state / "config.yaml").write_text("model:\n  provider: custom\n  base_url: %s\n  default: %s\n  context_length: 200000\n" % (base, mdl))
    else:
        base, mdl = "http://127.0.0.1:11434/v1", MODEL
        (state / "config.yaml").write_text("model:\n  provider: custom\n  base_url: http://127.0.0.1:11434/v1\n  default: %s\n"
                                           "  context_length: 65536\n  ollama_num_ctx: 65536\n" % MODEL)
    env = {"HOME": str(home), "HERMES_HOME": str(state), "HERMES_CWD": str(home), "TMPDIR": "/private/tmp",
           "PATH": "/usr/bin:/bin", "TERM": "dumb", "PYTHONDONTWRITEBYTECODE": "1"}
    replies, wall, errs, session = [], 0.0, [], None
    for turn in ask["turns"]:
        argv = [str(HERMES / ".venv/bin/python"), str(HERMES / "cli.py"), "-q", turn, "--oneshot", "--provider", "custom",
                "--base_url", base, "--model", mdl, "--api_key", "bench-loopback-only" if CLOUD["on"] else "ollama-local", "--quiet"]
        if session:
            argv += ["--resume", session]
        p, s = sandboxed(home, state, argv, env, CAP_S - wall)
        wall += s
        m = re.search(r"session_id:\s*(\S+)", p.stderr + p.stdout)
        session = session or (m.group(1) if m else None)
        out = re.sub(r"\n?session_id:\s*\S+\s*$", "", p.stdout.strip())
        replies.append(out)
        if p.returncode:
            errs.append(f"rc={p.returncode} {(p.stdout + p.stderr)[-300:]}")
        if p.returncode == "timeout":
            break
    return replies, wall, None, errs


VOOL_SB = {"on": False}


def vool_sb(home, state):
    """--vool-sandbox: VOOL under vool.sb, the rivals' rules plus read access to its own program files."""
    if not VOOL_SB["on"]:
        return []
    return ["sandbox-exec", "-f", str(M / "vool.sb"), "-D", f"HOME_CLONE={home}", "-D", f"STATE_DIR={state}"]


def run_voolcloud(ask, home, state):
    state.mkdir(parents=True, exist_ok=True)
    out = state / "row.json"
    env = dict(os.environ, HOME=str(home), VOOL_HOME=str(state / "vhome"), <legacy-name>_HOME=str(state / "vhome"), TMPDIR=str(M / "tmp"))
    (state / "vhome").mkdir(exist_ok=True)
    t = time.time()
    try:
        if VOOL_SB["on"]:
            env["TMPDIR"] = str(state / "tmp"); (state / "tmp").mkdir(exist_ok=True)
        subprocess.run(vool_sb(home, state) + [str(VOOL_PY), str(M / "vool_cloud_bench.py"), "--ask-json", json.dumps(ask), "--out", str(out),
                        "--proxy", CLOUD["proxy"], "--cap-s", str(CAP_S)], cwd=VOOL_REPO, env=env, timeout=CAP_S + 30,
                       capture_output=True, text=True)
        r = json.load(open(out))
        errs = [r["error"]] if r.get("error") else []
        if "TimeoutError" in str(r.get("error")):
            errs.append("timeout")
        return r["replies"], r["wall_s"], r.get("model_calls"), errs
    except subprocess.TimeoutExpired:
        return [], time.time() - t, None, ["timeout"]


def run_vool(asks, outdir):
    home = outdir / "vool-home"
    clone_home(home)
    before = owner_md5(home)
    cases = outdir / "vool-cases.jsonl"
    with open(cases, "w") as f:
        for a in asks:
            f.write(json.dumps({"id": a["id"], "job": "h2h-computer", "turns": a["turns"],
                                "expect": {"tool_any": ["machine.read_file"], "reply_must": a.get("reply_must", []), "reply_must_not": []},
                                "why": "h2h"}) + "\n")
    run = outdir / "vool-run"
    env = dict(os.environ, TMPDIR=str(outdir / "tmp")); (outdir / "tmp").mkdir(exist_ok=True)
    subprocess.run(["nice", "-n", "10"] + vool_sb(home, outdir) + [sys.executable, "-I", str(HARNESS), "--repo", str(VOOL_REPO), "--python", str(VOOL_PY),
                    "--model", MODEL, "--cases", str(cases), "--fakehome", str(home), "--out", str(run),
                    "--env", "VOOL_CLEAN_LOCAL_LOOP=1", "--env", f"VOOL_CLEAN_LOOP_MODEL={MODEL}"], env=env, check=False)
    after = owner_md5(home)
    rows = {}
    for r in map(json.loads, open(run / "raw.jsonl")):
        rows[r["id"]] = ([t.get("reply") or "" for t in r["turns"]], sum(t.get("seconds") or 0 for t in r["turns"]),
                         [f"http_{t.get('http_status')}" for t in r["turns"] if t.get("http_status") != 200])
    return rows, before, after


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--system", required=True, choices=["vool", "openclaw", "hermes", "voolcloud"])
    ap.add_argument("--cloud", action="store_true", help="rivals on OpenRouter GLM 5.3 flashx via or_proxy.py")
    ap.add_argument("--set", required=True, choices=["fset", "hset", "fresh", "r2"])
    ap.add_argument("--ids", default="")
    ap.add_argument("--tag", default="full")
    ap.add_argument("--vool-sandbox", action="store_true", help="run VOOL rows under vool.sb (same rules as rival.sb)")
    ap.add_argument("--latest", action="store_true", help="rivals at OpenClaw 2026.10.8 (node 26.12.1) / Hermes main c73b595")
    a = ap.parse_args()
    VOOL_SB["on"] = a.vool_sandbox
    if a.set == "r2":
        global FAKEHOME
        FAKEHOME = W / "missions/bench-win-20261028/round2/fakehome2"
    if a.latest:
        global OC, NODE_BIN, HERMES
        C = W / "cache/memory-h2h-20261005"
        OC, NODE_BIN, HERMES = (C / "openclaw-2026.10.8/node_modules/.bin/openclaw", C / "node-v26.12.1-darwin-arm64/bin",
                                C / "hermes-agent-c73b595")
    CLOUD["on"] = a.cloud or a.system == "voolcloud"
    asks = load_set(a.set)
    if a.ids:
        want = a.ids.split(",")
        asks = [x for x in asks if x["id"] in want]
    outdir = M / "runs" / a.tag
    outdir.mkdir(parents=True, exist_ok=True)
    out = outdir / f"{a.system}-{a.set}.jsonl"
    done = {json.loads(l)["id"] for l in open(out)} if out.exists() else set()
    todo = [x for x in asks if x["id"] not in done]
    if a.system == "vool":
        vrows, before, after = run_vool(todo, outdir / f"vool-{a.set}") if todo else ({}, None, None)
        with open(out, "a") as f:
            for x in todo:
                replies, wall, errs = vrows.get(x["id"], ([], 0, ["missing"]))
                timeout = wall > CAP_S or any("http_0" in e for e in errs)
                sc = score(x, replies) if not timeout else {"ok": False, "conf_wrong": False}
                f.write(json.dumps({"id": x["id"], "system": "vool", "replies": replies, "wall_s": round(wall, 1), "tokens": None,
                                    "timeout": timeout, "errors": errs, "home_md5_before": before, "home_md5_after": after,
                                    "added_files": added_files(outdir / f"vool-{a.set}" / "vool-home")[:40], "cloud": False, **sc}) + "\n")
    else:
        runner = {"openclaw": run_openclaw, "hermes": run_hermes, "voolcloud": run_voolcloud}[a.system]
        for x in todo:
            home = outdir / f"homes/{a.system}-{x['id']}"
            state = outdir / f"state/{a.system}-{x['id']}"
            clone_home(home)
            before = owner_md5(home)
            t_start = time.time()
            replies, wall, tokens, errs = runner(x, home, state)
            t_end = time.time()
            after = owner_md5(home)
            timeout = any("timeout" in e for e in errs) or wall > CAP_S
            sc = score(x, replies) if not timeout else {"ok": False, "conf_wrong": False}
            row = {"id": x["id"], "system": a.system, "replies": replies, "wall_s": round(wall, 1), "tokens": tokens,
                   "timeout": timeout, "errors": errs, "home_md5_before": before, "home_md5_after": after,
                   "added_files": added_files(home)[:40], "t_start": round(t_start, 3), "t_end": round(t_end, 3),
                   "cloud": CLOUD["on"], **sc}
            with open(out, "a") as f:
                f.write(json.dumps(row) + "\n")
            print(a.system, x["id"], "ok" if row["ok"] else ("CW" if row["conf_wrong"] else "miss"), f"{wall:.0f}s",
                  "home-changed" if before != after else "", flush=True)
    rows = [json.loads(l) for l in open(out)]
    n = len(rows); ok = sum(r["ok"] for r in rows); cw = sum(r["conf_wrong"] for r in rows)
    walls = sorted(r["wall_s"] for r in rows)
    to = sum(1 for r in rows if r.get("timeout"))
    print(f"{a.system} {a.set}: ok {ok}/{n} cw {cw} timeouts {to} wall p50 {walls[n // 2] if n else 0}s "
          f"home changed {sum(r['home_md5_before'] != r['home_md5_after'] for r in rows)}")


if __name__ == "__main__":
    main()
