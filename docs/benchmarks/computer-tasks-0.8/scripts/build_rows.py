#!/usr/bin/env python3
"""Per-answer table for every system/arm/version of the fresh-45 computer-task head-to-head -> rows-all.csv and
SUMMARY-TABLE.md. Every figure comes from a recorded source; a figure that cannot be recovered is written "n/a" and
the reason is listed in SUMMARY-TABLE.md (never estimated).

Sources: run rows (runs/<tag>/<system>-fresh.jsonl), the blind jury (../jury/key.jsonl + verdicts.jsonl), OpenRouter
usage per call (or_ledger.jsonl, matched to the row's [t_start, t_end] window and caller), VOOL's provider-reported
token_usage events (runs/<tag>/vool-fresh/vool-run/raw.jsonl), Hermes's own session DB (state/hermes-<id>/state.db),
OpenClaw's own transcript DB (state/openclaw-<id>/agents/main/agent/openclaw-agent.sqlite, zstd events decoded with
/opt/homebrew/bin/zstd). usage: python3 -I build_rows.py
"""
import csv, json, sqlite3, statistics, subprocess, tempfile
from datetime import datetime
from pathlib import Path

M = Path(__file__).resolve().parent
J = M.parent / 'jury'
ZSTD = '/opt/homebrew/bin/zstd'
# (label, system, version, arm, sandbox, tag, file, jury row label or None)
ARMS = [
    ('VOOL', 'fix14 39fc9527', 'local', 'none (harness, unsandboxed)', 'fresh', 'vool', 'S1'),
    ('VOOL', 'fix14 39fc9527 (rerun)', 'local', 'none (harness, unsandboxed)', 'fresh-rerun', 'vool', 'S2'),
    ('VOOL', 'fix14 39fc9527', 'local', 'vool.sb (= rival v3b rules)', 'fresh-sb', 'vool', 'L6'),
    ('VOOL', 'fix14 39fc9527 loop+tools', 'cloud', 'none (unsandboxed)', 'cloud', 'voolcloud', 'S3'),
    ('VOOL', 'fix14 39fc9527 loop+tools', 'cloud', 'vool.sb (= rival v3b rules)', 'cloud-sb', 'voolcloud', 'L5'),
    ('OpenClaw', '2026.9.6', 'local', 'v2 (reads open)', 'fresh', 'openclaw', 'S4'),
    ('OpenClaw', '2026.9.6', 'cloud', 'v2 (reads open)', 'cloud', 'openclaw', 'S5'),
    ('OpenClaw', '2026.10.8', 'local', 'v3b', 'fresh-latest', 'openclaw', 'L3'),
    ('OpenClaw', '2026.10.8', 'cloud', 'v3b', 'cloud-latest', 'openclaw', 'L1'),
    ('Hermes', '9a0a1625', 'local', 'v2 (reads open)', 'fresh', 'hermes', 'S6'),
    ('Hermes', '9a0a1625', 'cloud', 'v2 (reads open)', 'cloud', 'hermes', 'S7'),
    ('Hermes', 'c73b595', 'local', 'v3b', 'fresh-latest', 'hermes', 'L4'),
    ('Hermes', 'c73b595', 'cloud', 'v3b', 'cloud-latest', 'hermes', 'L2'),
    # round 2 (new home, new asks); headline rows, one sandbox profile each
    ('VOOL', 'fix14 39fc9527 loop+tools', 'cloud', 'vool.sb v3 (= rival v4 rules)', 'r2-cloud', 'voolcloud', 'R1'),
    ('OpenClaw', '2026.10.8', 'cloud', 'v4', 'r2-cloud-v4', 'openclaw', 'R2'),
    ('Hermes', 'c73b595', 'cloud', 'v4', 'r2-cloud-v4', 'hermes', 'R3'),
    ('VOOL', 'fix14 39fc9527', 'local', 'vool.sb v3 (= rival v4 rules)', 'r2-local', 'vool', 'R4'),
    ('OpenClaw', '2026.10.8', 'local', 'v4', 'r2-local', 'openclaw', 'R5'),
    ('Hermes', 'c73b595', 'local', 'v4', 'r2-local', 'hermes', 'R6'),
]
COLS = ['round', 'system', 'version', 'arm', 'sandbox', 'ask_id', 'regex_ok', 'jury_verdict', 'wrong_but_sure', 'unsafe_action', 'out_of_copy',
        'wall_s', 'model_calls', 'prompt_tokens', 'completion_tokens', 'cached_prompt_tokens', 'cache_write_tokens', 'cost_usd', 'timeout']
NA = 'n/a'


def ts(x):
    return datetime.fromisoformat(x.replace('Z', '+00:00')).timestamp() if isinstance(x, str) else float(x)


def jury():
    out = {}
    for jd in (J, M.parent / 'jury-r2'):
        key = {k['item']: k for k in map(json.loads, open(jd / 'key.jsonl'))} if (jd / 'key.jsonl').exists() else {}
        if (jd / 'verdicts.jsonl').exists():
            for v in map(json.loads, open(jd / 'verdicts.jsonl')):
                if v['judge'] == 'claude-opus-5-5' and v['verdict'] and v['item'] in key:
                    k = key[v['item']]; out[(k['row'], k['ask'])] = v
    return out


USED = set()
LEDGER = [json.loads(l) for l in open(M / 'or_ledger.jsonl')] if (M / 'or_ledger.jsonl').exists() else []


def cloud_usage(row, system):
    a, b = ts(row['t_start']) - 1, ts(row['t_end']) + 1
    want = 'voolcloud' if system == 'voolcloud' else ''
    calls = [l for l in LEDGER if a <= l['t'] <= b and (l.get('client') or '') == want and l.get('status') == 200 and id(l) not in USED]
    USED.update(id(l) for l in calls)  # a call at a window edge is counted for one row only
    if not calls:
        return dict(model_calls=0, prompt_tokens=0, completion_tokens=0, cached_prompt_tokens=0, cache_write_tokens=NA, cost_usd=0.0)
    return dict(model_calls=len(calls), prompt_tokens=sum(l['prompt_tokens'] or 0 for l in calls), completion_tokens=sum(l['completion_tokens'] or 0 for l in calls),
                cached_prompt_tokens=sum(l.get('cached_tokens') or 0 for l in calls), cache_write_tokens=NA, cost_usd=round(sum(l['cost'] for l in calls), 6))


def vool_local(tag):
    out = {}
    p = M / 'runs' / tag / ('vool-r2' if tag.startswith('r2-') else 'vool-fresh') / 'vool-run/raw.jsonl'
    if not p.exists():
        return out
    for r in map(json.loads, open(p)):
        done = [e for e in r['events'] if e.get('event_type') == 'model.call_completed']
        tu = [e.get('token_usage') or {} for e in done]
        out[r['id']] = dict(model_calls=len(done), prompt_tokens=sum(t.get('input_tokens') or 0 for t in tu),
                            completion_tokens=sum(t.get('output_tokens') or 0 for t in tu), cached_prompt_tokens=NA, cache_write_tokens=NA, cost_usd=0.0)
    return out


def hermes_db(tag, aid):
    db = M / 'runs' / tag / 'state' / f'hermes-{aid}' / 'state.db'
    if not db.exists():
        return None
    tmp = tempfile.mkdtemp()
    for suffix in ('', '-wal', '-shm'):
        if Path(str(db) + suffix).exists():
            subprocess.run(['cp', str(db) + suffix, f'{tmp}/a.db{suffix}'], check=True)
    c = sqlite3.connect(f'{tmp}/a.db')
    r = c.execute('select coalesce(sum(api_call_count),0), coalesce(sum(input_tokens),0), coalesce(sum(output_tokens),0), '
                  'coalesce(sum(cache_read_tokens),0), coalesce(sum(cache_write_tokens),0) from sessions').fetchone()
    return dict(model_calls=r[0], prompt_tokens=r[1], completion_tokens=r[2], cached_prompt_tokens=r[3], cache_write_tokens=r[4])


def openclaw_db(tag, aid):
    db = M / 'runs' / tag / 'state' / f'openclaw-{aid}' / 'agents/main/agent/openclaw-agent.sqlite'
    if not db.exists():
        return None
    tmp = tempfile.mkdtemp()
    for suffix in ('', '-wal', '-shm'):  # read a private copy: the live DB is WAL-mode and may be open
        if Path(str(db) + suffix).exists():
            subprocess.run(['cp', str(db) + suffix, f'{tmp}/a.sqlite{suffix}'], check=True)
    try:
        c = sqlite3.connect(f'{tmp}/a.sqlite')
        c.execute('select 1 from transcript_events limit 1')
    except Exception as e:
        return dict(model_calls=NA, prompt_tokens=NA, completion_tokens=NA, cached_prompt_tokens=NA, cache_write_tokens=NA, _err=f'{type(e).__name__}')
    n = i = o = cr = cw = 0
    for ej, z in c.execute('select event_json, event_zstd from transcript_events order by seq'):
        if not ej and z:
            with tempfile.NamedTemporaryFile() as f:
                f.write(z); f.flush()
                ej = subprocess.run([ZSTD, '-dqc', f.name], capture_output=True).stdout.decode('utf-8', 'replace')
        try:
            m = (json.loads(ej) or {}).get('message') or {}
        except Exception:
            continue
        u = m.get('usage')
        if m.get('role') == 'assistant' and isinstance(u, dict):
            n += 1; i += u.get('input') or 0; o += u.get('output') or 0; cr += u.get('cacheRead') or 0; cw += u.get('cacheWrite') or 0
    return dict(model_calls=n, prompt_tokens=i + cr + cw, completion_tokens=o, cached_prompt_tokens=cr, cache_write_tokens=cw)


def main():
    jv = jury()
    rows, notes = [], set()
    for system, version, arm, sandbox, tag, sysname, jlabel in ARMS:
        rnd = '2' if tag.startswith('r2-') else '1'
        f = M / 'runs' / tag / f"{sysname}-{'r2' if rnd == '2' else 'fresh'}.jsonl"
        if not f.exists():
            notes.add(f'{system} {version} {arm} ({tag}): not run yet / no rows file'); continue
        vl = vool_local(tag) if sysname == 'vool' else {}
        for r in map(json.loads, open(f)):
            v = jv.get((jlabel, r['id'])) if jlabel else None
            d = dict(round=rnd, system=system, version=version, arm=arm, sandbox=sandbox, ask_id=r['id'], regex_ok=int(bool(r['ok'])),
                     jury_verdict=v['verdict'] if v else NA, wrong_but_sure=(int(v['verdict'] == 'wrong_sure') if v else NA),
                     unsafe_action=(int(v['verdict'] == 'unsafe_action') if v else NA), out_of_copy=(int(bool(v.get('out_of_copy'))) if v else NA),
                     wall_s=r.get('wall_s'), timeout=int(bool(r.get('timeout'))))
            if not v:
                notes.add(f'{system} {version} {arm} ({tag}): jury not run on this row yet')
            if arm == 'cloud':
                d.update(cloud_usage(r, sysname))
                if sysname == 'voolcloud' and isinstance(r.get('tokens'), int):
                    d['model_calls'] = r['tokens']  # vool_cloud_bench reports model_calls in the tokens slot
                notes.add('cloud: cache_write_tokens n/a (OpenRouter usage for GLM 5.3 flashx reports cached reads only)')
            elif sysname == 'vool':
                d.update(vl.get(r['id']) or dict(model_calls=NA, prompt_tokens=NA, completion_tokens=NA, cached_prompt_tokens=NA, cache_write_tokens=NA, cost_usd=0.0))
                notes.add('VOOL local: tokens = provider-reported token_usage on model.call_completed events; Ollama reports no cache figures (n/a)')
            elif sysname == 'hermes':
                h = hermes_db(tag, r['id'])
                d.update(h or dict(model_calls=NA, prompt_tokens=NA, completion_tokens=NA, cached_prompt_tokens=NA, cache_write_tokens=NA), cost_usd=0.0)
                notes.add('Hermes local: tokens from its own sessions table (input/output/cache_read/cache_write, api_call_count)')
            else:
                o = openclaw_db(tag, r['id'])
                if o and o.pop('_err', None):
                    notes.add(f'OpenClaw {version} {arm} ({tag}) {r["id"]}: transcript DB unreadable -> n/a')
                d.update(o or dict(model_calls=NA, prompt_tokens=NA, completion_tokens=NA, cached_prompt_tokens=NA, cache_write_tokens=NA), cost_usd=0.0)
                notes.add('OpenClaw local: tokens from its own transcript usage per assistant message (input+cacheRead+cacheWrite = prompt)')
            rows.append(d)
    with open(M / 'rows-all.csv', 'w', newline='') as fh:
        w = csv.DictWriter(fh, fieldnames=COLS); w.writeheader(); w.writerows(rows)
    num = lambda xs: [x for x in xs if isinstance(x, (int, float))]
    out = ['# Fresh-45 computer tasks: per-system totals and per-answer averages', '',
           'One row per system/version/arm/sandbox. jury = blind opus-5-5 (S/L rows in ../jury). n/a = not recoverable (reasons below).', '',
           '| round | system | version | arm | sandbox | n | regex ok | jury correct | wrong-sure | unsafe | out-of-copy | timeouts | wall p50 s | model calls/ans | prompt tok/ans | completion tok/ans | cached tok/ans | cost $ total | $/ans |',
           '|' + '---|' * 19]
    for system, version, arm, sandbox, tag, sysname, jl in ARMS:
        g = [r for r in rows if (r['system'], r['version'], r['arm'], r['sandbox'], r['round']) == (system, version, arm, sandbox, '2' if tag.startswith('r2-') else '1')]
        if not g:
            continue
        avg = lambda k: (round(statistics.mean(num([r[k] for r in g])), 1) if len(num([r[k] for r in g])) == len(g) else NA)
        jc = [r for r in g if r['jury_verdict'] != NA]
        cost = sum(num([r['cost_usd'] for r in g]))
        out.append(f"| {g[0]['round']} | {system} | {version} | {arm} | {sandbox} | {len(g)} | {sum(r['regex_ok'] for r in g)} | "
                   f"{sum(r['jury_verdict'] == 'correct' for r in jc) if jc else NA} | {sum(r['wrong_but_sure'] for r in jc) if jc else NA} | "
                   f"{sum(r['unsafe_action'] for r in jc) if jc else NA} | {sum(r['out_of_copy'] for r in jc) if jc else NA} | {sum(r['timeout'] for r in g)} | "
                   f"{round(statistics.median(num([r['wall_s'] for r in g])), 1)} | {avg('model_calls')} | {avg('prompt_tokens')} | {avg('completion_tokens')} | "
                   f"{avg('cached_prompt_tokens')} | {round(cost, 4)} | {round(cost / len(g), 5)} |")
    out += ['', '## Sources and n/a reasons', ''] + [f'- {n}' for n in sorted(notes)]
    (M / 'SUMMARY-TABLE.md').write_text('\n'.join(out) + '\n')
    print('\n'.join(out))


if __name__ == '__main__':
    main()
