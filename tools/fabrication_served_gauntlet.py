#!/usr/bin/env python3
"""Boot an isolated served daemon on a loopback scripted provider and run the gauntlet at it.

This is the /api/chat entrance of the fabrication matrix. It exists so the second entrance can
be measured WITHOUT a real model and WITHOUT network egress -- the blueprint's deterministic
half. A scripted provider proves protocol only; it is explicitly NOT a real-model proof and no
run produced here may be quoted as one.

Isolation, all of it required and all of it reported:
  * synthetic HOME / VOOL_HOME / TMPDIR under one scratch root, removed at the end;
  * the daemon gets its own port AND its own mesh bind port;
  * EVERY local model endpoint is repointed at the stub -- OLLAMA_HOST, VOOL_OLLAMA_URL and
    VOOL_OLLAMA_CHAT_URL. Repointing only some of them lets a lane reach the operator's real
    Ollama on 11434, which is both a wrong measurement and a live model launch this machine
    forbids;
  * no Keychain: VOOL_KEY_STORAGE_MODE=file with a scratch passphrase.

Usage:
    python tools/fabrication_served_gauntlet.py --out validation-logs/<lane>/ [--prompt both]
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

PROBE = "probe-certified:2b"  # the ":2b" matters: the resource governor sizes from the NAME.

_SEED = textwrap.dedent(
    '''
    import sys
    sys.path.insert(0, "{root}")
    from storage.db import get_connection
    from storage.migrations import run_migrations
    from storage.model_provider_manifest import (
        ModelProviderManifest, list_provider_manifests, upsert_provider_manifest,
    )
    from tests._authorship_certification import certify_for_authorship

    run_migrations()

    def manifest(name):
        return ModelProviderManifest(
            provider_name="ollama-local", model_name=name, source_type="http",
            adapter_type="openai_compatible", license_name="Apache-2.0",
            license_reference="https://ollama.com/library/qwen3",
            weight_location="external", runtime_dependency="ollama",
            capabilities=["summarize", "classify", "format", "extract", "structured_json"],
            runtime_config={{"base_url": "{base_url}", "timeout_seconds": 30}},
            metadata={{
                "runtime_family": "ollama", "cost_class": "free_local",
                "model_digest": "sha256:stub", "chat_template_hash": "tmpl",
                "quantization": "q4_K_M", "parameter_billions": 2.0,
            }},
        )

    conn = get_connection()
    conn.execute("DELETE FROM model_provider_manifests")
    conn.commit()
    conn.close()
    for name in {registered!r}:
        upsert_provider_manifest(manifest(name))
    for name in {certified!r}:
        certify_for_authorship(manifest(name))
    print(sorted(m.provider_id for m in list_provider_manifests()))
    '''
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="directory for the capture json + logs")
    ap.add_argument("--prompt", default="both")
    ap.add_argument("--boot-timeout", type=float, default=300.0)
    args = ap.parse_args()

    from tests._authorship_served_rig import ScriptedProvider, ServedDaemon, seed_in_home

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    sha = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout.strip()

    scratch = Path(tempfile.mkdtemp(prefix="fab-served-"))
    home = scratch / "home"
    home.mkdir(parents=True, exist_ok=True)
    (scratch / "tmp").mkdir(parents=True, exist_ok=True)

    provider = ScriptedProvider({PROBE: "ack"})
    provider.__enter__()
    daemon = None
    try:
        daemon = ServedDaemon(
            home,
            env_extra={
                "HOME": str(home),
                "TMPDIR": str(scratch / "tmp"),
                # Every local endpoint, not some of them.
                "OLLAMA_HOST": provider.base_url,
                "VOOL_OLLAMA_URL": provider.base_url,
                "VOOL_OLLAMA_CHAT_URL": provider.base_url + "/api/chat",
            },
        )
        daemon.start(timeout=args.boot_timeout)
        seed_in_home(
            home,
            _SEED.format(
                root=REPO, base_url=provider.base_url, registered=[PROBE], certified=[PROBE]
            ),
        )
        provider.reset()  # drop the boot-time health probes so counts mean something

        capture = out_dir / f"red_b_api_chat_{sha}.json"
        log = out_dir / f"red_b_api_chat_{sha}.out"
        proc = subprocess.run(
            [
                sys.executable, "tests/red_b_served_gauntlet.py",
                "--entrance", "api_chat",
                "--base-url", daemon.base_url,
                "--prompt", args.prompt,
                "--out", str(capture),
            ],
            cwd=REPO,
            capture_output=True,
            text=True,
            env={
                **__import__("os").environ,
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONPATH": str(REPO),
                "VOOL_HOME": str(home),
                "HOME": str(home),
                "TMPDIR": str(scratch / "tmp"),
            },
            timeout=3600,
        )
        log.write_text((proc.stdout or "") + (proc.stderr or ""))
        print((proc.stdout or "")[-4000:])
        print(f"\nwrote {capture}\nwrote {log}")
        meta = {
            "sha": sha,
            "entrance": "api_chat",
            "provider": "ScriptedProvider (loopback stub) -- protocol only, NOT a real-model run",
            "daemon_base_url": daemon.base_url,
            "stub_base_url": provider.base_url,
            "stub_generations": provider.generations_for(PROBE),
            "isolated_home": str(home),
            "returncode": proc.returncode,
        }
        (out_dir / f"red_b_api_chat_{sha}.meta.json").write_text(json.dumps(meta, indent=2) + "\n")
        return proc.returncode
    finally:
        if daemon is not None:
            try:
                daemon.stop()
            except Exception:
                pass
        try:
            provider.__exit__(None, None, None)
        except Exception:
            pass
        shutil.rmtree(scratch, ignore_errors=True)
        print(f"cleanup: scratch removed = {not scratch.exists()}")


if __name__ == "__main__":
    raise SystemExit(main())
