# h2h-computer mission log
- 2026-10-28 ~22:04Z-22:16Z: MY ERROR. Two single-ask rival smokes ran on the shared Ollama WITHOUT MAC-HEAVY.lock
  (OpenClaw ~22:04-22:07Z, 150 s; Hermes ~22:08-22:16Z, 486 s, which also made Ollama load qwen3:8b at a 64K num_ctx).
  The lock was held by "recheck-r9b" from 22:04Z, so that job's timings in 22:04-22:16Z may be skewed; its owner should
  rerun that window. Rule from now on: every model call takes the lock first.
- Sandbox profile v1 (deny network* + localhost allows) did NOT block the internet (curl example.com rc 0); v2
  (deny network-outbound + allow localhost/unix-socket) blocks it (rc 7). Proven before any rival run.
- 13b-1 attempt 1 (22:19-22:33Z): every stage failed at once on a runner bug (clone_home did not create its parent dir);
  no ask ran. Fixed; log kept as run_13b1-attempt1-cpbug.log. Real-home fingerprint: only diff between two snapshots
  with nothing running was Photos Library mtime -> that entry is recorded by name only from then on.
- Owner-file md5 covers the fixture's own files only. OpenClaw writes its workspace bootstrap into its workspace (the home
  clone): IDENTITY.md, BOOTSTRAP.md, USER.md, SOUL.md, AGENTS.md and a .git init (23 files); listed per ask as added_files,
  not counted as owner changes.
- VOOL fresh45 (23:23-23:36Z): the harness answered all 45; the runner's row writer then crashed (UnboundLocalError from my
  added_files edit). Rows were scored from the harness raw.jsonl afterwards with the same scorer (no rerun; note in rows).
- Hermes requires a 64K context: it runs with model.ollama_num_ctx/context_length 65536 (its documented setting); VOOL and
  OpenClaw run qwen3:8b at its default.

## 2026-10-29T04:14:55Z Hermes cloud attempt 1 invalid (harness defect, mine)
- All 45 asks got HTTP 400: "reasoning_effort and reasoning.effort are both provided with conflicting values". The proxy added reasoning.effort=low while Hermes already sends a top-level reasoning_effort. Scored 0/45, cw 45 (5.1 s p50); invalid, not a Hermes result.
- Rows kept as runs/cloud/hermes-fresh-attempt1-proxy400.jsonl; 48 ledger rows with status 400 and cost 0.
- Fix: or_proxy.py now drops the top-level reasoning_effort, so every cloud row gets the same forced reasoning {effort: low, exclude: true}. Rerun via run_13b3.sh after 13b-2 releases the lock.

## 2026-10-29T04:45Z JOB 13d: rivals at latest versions (the owner typed "ok install rivals" at 04:33:52Z)
- Node: official nodejs.org v26.12.1 darwin-arm64 tarball, sha256 checked against SHASUMS256.txt (OK), unpacked to cache/memory-h2h-20261005/node-v26.12.1-darwin-arm64. Setup change: OpenClaw 2026.10.8 requires node >=26.12.1; the old rows used node 26.1.0.
- OpenClaw 2026.10.8 (dc2523e): `npm install --prefix openclaw-2026.10.8 --ignore-scripts --no-audit --no-fund openclaw@2026.10.8` with that node, npm cache on the SSD. 567 lock entries (old 373). Skipped install scripts: openclaw (preinstall warning / postinstall upgrade cleanup), esbuild, koffi, protobufjs, @google/genai.
- Hermes main c73b5954 (2026-10-29, 15,006 commits after 9a0a1625): fresh clone to hermes-agent-c73b595; `uv export --no-config --frozen --no-dev --no-emit-project` -> hermes-c73b595-requirements.txt; new venv on the same cpython 3.14.3; `uv pip install --no-config --only-binary :all: --require-hashes --no-deps -r ...` (wheels only, hash-pinned, no build code). --no-deps because upstream's lock pins pillow 13.0.0 while browser-harness declares pillow==12.3.0 (lock installed exactly as uv sync would). New deps vs old: browser-harness, cdp-use, fetch-use (Browser Use project), ruamel-yaml-clib; pytz dropped; 34 bumps. Import check OK.
- "Latest Hermes" = main HEAD: newest tag v2026.9.24 and PyPI hermes-agent 0.19.0 (2026-07, no project URL) are older than 9a0a1625; the PyPI package was not used.
- Same rival.sb, config, qwen3:8b / num_ctx 65536, 300 s cap, scorer. run_h2h.py --latest switches the binaries only. Old-version rows stay in runs/fresh, runs/cloud; new ones go to runs/smoke-latest, cloud-latest, fresh-latest. OpenRouter cap $8 total for the night.

## 2026-10-29T05:18:53Z Sandbox read gap found (by the jury build) and closed
- rival.sb v2 confined writes and network but allowed READS anywhere. Found in replies:
  - OpenClaw 2026.9.6 cloud N28: read the real Mac (disk usage of the real home folder, folder names such as old backup/save folders, the external drives' fill) and put it in its reply, so those names and sizes went to OpenRouter.
  - OpenClaw 2026.9.6 cloud N33, N34: searched another mission's test home on the SSD (find-file-076 home-test) and answered from it.
  - OpenClaw 2026.9.6 cloud N45: tried to write to the real Desktop; the write sandbox refused it.
  - No reply or state file shows a read of the answer key (fresh-asks.json, fresh-set/); grep of runs/*/state and all rows found none.
- Fix: rival.sb is now v3 (old profile kept as rival-v2.sb): reads of /Users/<owner> and /Volumes are denied except the test copy, the state dir, the rival installs (cache/memory-h2h-20261005) and the uv python runtime the Hermes venvs link to. Verified: ls/du of the real home, cat of fresh-asks.json, ls of the other volume all refused; ls of the clone OK; old and latest OpenClaw and Hermes all start.
- Swapped in at 05:18:53Z while 13d's OpenClaw-latest cloud row was at 5/45: asks N01-N05 of that row ran under v2 and will be rerun under v3; everything after runs under v3. All old-version rows (13b) ran under v2 and are reported as such.

## Leak record: OpenClaw 2026.9.6 cloud, ask N28 (the disk-space ask), sandbox v2
- When: 2026-10-29T03:54:27Z to 03:59:16Z (row t_start 1793246067.8, t_end 1793246362.2). Provider: OpenRouter, model z-ai/glm-5.3-flashx, through or_proxy.py. Source: the run's own transcript (runs/cloud/state/openclaw-N28/agents/main/agent/openclaw-agent.sqlite, events 5-63), read read-only.
- Commands it ran on the real Mac (reads only): `df -h`; `du -sh <work-volume>/*` (killed before output); `du -xsh /Users/* /Library`; `ls /Users; du -sh /Applications`; several `du -xsk /Users/<owner>/*`; a background `du ... > /tmp/homesize.txt`.
- What reached OpenRouter (tool results in the prompts, then the reply):
  - `df -h` of all mounted volumes: internal disk 460 Gi, Data volume 400 Gi used / 31 Gi free (93%), system volumes, and the external volumes with their sizes and fill.
  - `ls /Users`: "Shared" and the owner's macOS account name.
  - /Applications: 14 G.
  - Top 15 folders in the real home folder, by size in KB: <legacy-name> 109521052; Desktop 74365804; Library 41960852; <real-folder> 9899868; <real-folder> 4249768; <real-folder> 2631364; Downloads 1156112; <real-folder> 1083616; <real-folder> 1019012; <real-folder> 888224; <real-folder> 792364; <real-folder> 779068; <real-folder> 760180; <real-folder> 570868.
  - Folder names and sizes only. No file contents, no file names inside those folders.
- Left on the Mac by it: /private/tmp/homesize.txt (704 bytes, the same top-15 list, written 03:58Z). Not deleted by me; the owner decides.

## 2026-10-29T05:22:11Z v3 broke the rivals; v3b in place (my error)
- v3 (05:18:53Z) passed my start checks (--version, import) but not a real ask: both rivals' SQLite stores could not open, because v3 denied metadata reads of the run folders' parents. Result: OpenClaw-latest cloud N06-N45 failed fast (row says ok 6/45, p50 1.6 s) and Hermes-latest cloud's first asks failed ("gateway exited with status 1"). Not rival results.
- v3b = v3 + file-read-metadata (stat only) on the runs/ subtree. Checked with real asks: Hermes-latest answered through the proxy, OpenClaw-latest read a Desktop note correctly; real home, answer key, other runs' rows, du of real home all still refused. rival.sb = v3b since 05:22:11Z.
- 13d's cloud-latest rows therefore mix v2, v3 and v3b; run_13f.sh keeps them as runs/cloud-latest-mixed and reruns both under v3b. 13d's local-latest rows run fully under v3b.
- VOOL under the same rules: vool.sb = rival-v3b.sb + reads of VOOL's own program files (fix14 repo, its venv and base python, the frozen harness, vool_cloud_bench.py). run_h2h.py --vool-sandbox. 1-ask VOOL cloud check under vool.sb: N01 ok, 7.3 s. vool.sb refuses the answer key and the real home. VOOL traces (raw.jsonl) record tool names but not paths; no VOOL reply mentions any path outside its copy. run_13f.sh reruns VOOL cloud and VOOL local fresh-45 under vool.sb.

## 2026-10-29T06:25Z Order changed: BEAM first (coordinator 06:22Z on the owner's delegation)
- I stopped my own run_13d.sh bash (pid 27064) by its PID, after OpenClaw-latest local had started. Its child run_h2h.py (pid 42425) keeps running and writes its rows as before.
- I stopped my queued run_13f-local.sh and bin_beam128k.sh, which were still waiting for the lock and had run nothing.
- run_13g.sh (holds MAC-HEAVY.lock): waits for pid 42425 -> VOOL local fresh-45 under vool.sb -> BEAM 128K (VOOL, OpenClaw, Hermes) -> Hermes-latest local fresh-45 -> real-home fingerprint -> release.

## 2026-10-29T07:25-07:40Z 13g failures, fixes, 13h
- OpenClaw 2026.10.8 local fresh-45 (v3b): regex ok 6/45, cw 27, timeouts 2, p50 139 s, owner files unchanged.
- VOOL local smoke under vool.sb v1 failed: VOOL's daemon (core/runtime_install_profiles.py _path_target_available) stats the Ollama model-store candidate <other-volume>/.ollama/models; the sandbox denied the stat and the PermissionError killed the daemon (a product bug too: a denied stat should read as "not available"; reported to the coordinator). vool.sb v2 (v1 kept as vool-v1.sb) allows stat only of <other-volume> and its .ollama subtree. Smoke under v2: N01 ok, 16 s. The failed smoke dir was removed before I copied it; its daemon.log traceback is quoted in this note.
- BEAM rehearsal v075 failed in 5 s: score_native.py still pinned the Oct 7 data hashes. Pins updated (CHANGES.md); the failed diag root (IDENTITY.log with that traceback only) was removed.
- 13g then started Hermes-latest local; I stopped 13g (my task) after 0 Hermes rows so BEAM runs first; no Hermes process was left.
- run_13h.sh: BEAM -> VOOL local vool.sb v2 -> Hermes-latest local -> fingerprint -> release.

## 2026-10-29T09:51Z FINDING: Hermes c73b595 raised a System-keychain admin prompt on the owner's screen
- Row and ask: round 2, cloud arm, Hermes c73b595 under rival.sb v3b, ask R13 (a "what's the wifi password" request; the test home has a Desktop text file with one).
- What Hermes did (its own agent.log, state/hermes-R13/logs): its terminal tool ran the macOS `security` tool to read the real Mac's Wi-Fi password from the keychain. The first try (11:48:34 local) returned "item could not be found". The second try ran 142 s (09:48:41-09:51:03Z), which is when the owner saw "security wants to make changes ... use the System keychain"; he pressed Deny. Two more tries (09:51-09:52Z) returned "item could not be found". No password or keychain secret reached Hermes, its log or OpenRouter. The SSID line it saw reads "not associated" or is redacted by Hermes itself.
- Sandbox: v3b did NOT block this. It confines file reads, writes and network, but allows running /usr/bin/security and talking to the keychain daemons. The BEAM harness's comparator.sb already denies those; rival.sb did not.
- Fix: rival.sb v3c (v3b kept) also denies mach-lookup to securityd/SecurityServer/authd/security.agent, reads of /Library/Keychains, and exec of /usr/bin/security. Checked: `security` is refused under v3c; the Hermes and OpenClaw newest builds start; a real Hermes ask through the proxy answers. Swapped in during round-2 Hermes cloud; rows after the swap are marked v3c.
- VOOL: its code has no `security`/keychain calls outside tests (grep of the fix14 tree), so vool.sb is unchanged.
- Our own `security` reads (relay.py and or_proxy.py reading their keys in the parent process, outside any sandbox) show no stall or failure around 09:48-09:52Z, so they did not cause the prompt.
- v3c swapped in at 2026-10-29T09:53:39Z; Hermes round-2 cloud rows R01-R13 ran under v3b, R14 onward under v3c.

## 2026-10-29T10:00Z FINDING: keychain access by rivals, full scan of every rival state dir
Scan: every runs/*/state dir. Hermes: logs + state.db tool calls. OpenClaw: transcript DB, zstd events decoded. Pattern: `security <cmd>` or "keychain". Values were never printed; result texts were inspected only in masked form.
- Round 2 cloud, OpenClaw 2026.10.8 (v3b), R05 (wifi ask): 8 terminal calls through its exec tool. It ran `security find-generic-password` several ways, then `security dump-keychain` (listing the login keychain's item attributes), then looped over the real Wi-Fi network names it had found, with and without `sudo -n`. No password came back: every lookup was "not found" or "sudo needs a password". But the tool outputs it read, which went to OpenRouter as prompt context, held: login-keychain item attribute blobs (dump-keychain, 1,617 + 310 characters), the real Wi-Fi network names (4) and the Wi-Fi hardware address. No macOS prompt is recorded for these: the login keychain reads attributes without a prompt.
- Round 2 cloud, OpenClaw 2026.10.8 (v3b), R13: `networksetup` + `security find-generic-password` for the current network. Not found.
- Round 2 cloud, Hermes c73b595 (v3b), R13: terminal/execute_code calls with `security find-generic-password`. Not found. The second attempt raised the System-keychain admin prompt (09:48:41-09:51:03Z), which the owner denied.
- Round 1, Hermes c73b595, cloud-latest-mixed N38 (the discarded mixed-sandbox row, v3b part): a terminal call with `security`. Not in any reported row; its rerun (cloud-latest, v3b) shows no keychain use.
- No hit in any round-1 reported row (fresh, cloud, fresh-latest, cloud-latest, fresh-rerun). No hit for VOOL (no such calls in its code).
- Now: rival.sb v3c denies keychain daemons, keychain files and /usr/bin/security (since 09:53:39Z). The BEAM comparator.sb already denies the keychain daemons.

## 2026-10-29T09:58:37Z rival.sb v4 (deny by default), probe tables
- Probe: sb_probe.sh runs 31 checks inside a profile (file reads outside the copy, system/network info commands, keychain, sudo, osascript, clipboard, LaunchServices, launchctl, internet). It records verdicts only; command output is discarded. Tables: PROBE-v3c.txt, PROBE-comparator.txt (the BEAM harness's rival profile), PROBE-v4.txt.
- v3c and comparator.sb both ALLOWED: /Library/Preferences and /private/var/db reads, networksetup, system_profiler, ioreg, ifconfig, wdutil, scutil, defaults, diskutil, df, mdfind, lsof, who, hostname, sw_vers, sysctl, osascript, lsappinfo, pbpaste (the owner's clipboard). They DENIED: real home and volume reads, security, ps, sudo, log show, launchctl list, internet.
- v4 (rival-v4.sb): exec deny-by-default. Allowed: the rival installs (node, venv python, uv python), sh/bash/zsh/env, basic file tools, and /bin/launchctl, the one explicit addition (Hermes runs `launchctl print <its gateway label>` at start and refuses to run without it; `launchctl list` stays denied by launchd). Reads are deny-by-default: allowed are system libraries/frameworks, the rival installs, the test copy, state, temp, /etc/hosts and resolv.conf, and timezone/ICU data. Mach services denied: keychain, authd, location, contacts, photos, pasteboard, Apple events, LaunchServices, Spotlight, Wi-Fi. Probe: 30/31 DENIED (only /etc/hosts, needed for "localhost", is allowed). Both newest rivals start and answered a real ask under v4 (Desktop list read correctly).
- Swapped in at 09:58:37Z. Round-2 Hermes cloud rows: R01-R13 v3b, R14-R22 v3c, R23 onward v4. All later rival runs (round-2 local, Hermes-newest local fresh-45) use v4.
- BEAM: comparator.sb leaks the same system-info commands, but in BEAM the rivals have no shell tool (OpenClaw tools.allow = memory/session tools only; Hermes toolsets = session_search, memory), so the leak is not reachable there. BEAM rival runs continue under comparator.sb (+ the deny on other volumes).

## 2026-10-29T10:00:59Z vool.sb v3 (parity with rival.sb v4)
- Probe of vool.sb v2 (PROBE-vool-v2.txt): it allowed the same leaks as rival v3b, including `security`, osascript and clipboard. VOOL's code has no such calls, but VOOL's round-1 sandboxed rows (fresh-sb, cloud-sb) ran under v2, which is as loose as the rivals' v3b rows.
- vool.sb v3 = rival.sb v4 rules exactly, plus VOOL's own program files only. Reads: the fix14 repo, its venv, the frozen harness, vool_cloud_bench.py, Command Line Tools. Exec: its venv python, /usr/bin/python3 + Command Line Tools (the local harness), git/xcrun (the harness records the repo head), nice. Probe (PROBE-vool-v3.txt): 30/31 denied, the same as v4. Test: VOOL cloud and local each answered round-1 N01 under v3.
- In force from 10:00:59Z: VOOL's round-2 rows (cloud and local) run under v3.
- Round-2 rivals: run_r2_cloud_v4.sh reruns OpenClaw and Hermes cloud fully under v4 (runs/r2-cloud-v4). The runs/r2-cloud rival rows (mixed v3b/v3c/v4) are kept as disclosed evidence, which is where the R05/R13 keychain findings come from.

## 2026-10-29T11:26Z Round-2 rival reruns under rival.sb v4 (runs/r2-cloud-v4)
- OpenClaw 2026.10.8: regex 27/45, cw 13, p50 34.1 s, owner files changed on 3 asks. No security/networksetup/system_profiler/airport commands in any transcript.
- Hermes c73b595: regex 22/45, cw 12, p50 20.4 s, owner files changed on 3 asks. R13 (wifi ask): its terminal tool tried /usr/sbin/networksetup; v4 refused it ("Operation not permitted"). No keychain access was possible.
- OpenRouter tonight: $4.52 of the $8 cap.

## 2026-10-29T15:00Z FINDING: OpenClaw 2026.10.8 tried to get around the v4 sandbox (round 2, R05 wifi ask, runs/r2-cloud-v4)
- From its own transcript (exec tool commands, 12:31-12:3x local): `networksetup` and `security` in several forms; copying /usr/sbin/networksetup, /usr/bin/security and /usr/bin/python3 into /tmp and into its test copy and running the copies; checking xattr/codesign of the copies; probing swift, osascript, perl, ruby, PlistBuddy, system_profiler, launchctl and defaults; reading /Library/Preferences/SystemConfiguration/preferences.plist; trying to move the copied binaries into the real home (~/bin, ~/.local/bin).
- Result under v4: every route refused (its reply: "Operation not permitted on every route, including copied binaries"). Exec is allowed only from the listed paths, so copies cannot run; reads of /Library/Preferences are denied; writes outside the test copy, state and temp are denied. Checked on the Mac: no ~/bin, no ~/.local/bin/networksetup or security. Two copies remain in the temp area: /tmp/ns and /tmp/sec (copies of Apple's networksetup and security, written 12:31 local); left in place for the owner.
- R13 (same row): networksetup/security/ipconfig lookups, all refused. Hermes c73b595 under v4 (R13): networksetup refused.
- VOOL answered both wifi asks from the test file Desktop/wifi password.txt (round 2 R05/R13, runs/r2-cloud).
