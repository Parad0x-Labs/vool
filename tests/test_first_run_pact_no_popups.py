"""M-P6/P9 — the zero-popup fence: the entire tour performs ZERO credential/Keychain calls.

Extends the landed C15 fence (tests/_credential_probe.py) to the first-run pact journey:
fake `security`/`keyring` binaries sit FIRST on PATH and record every invocation; a full
tour (seed → card → naming → facts → boundaries → provider choice → claims) with the card
visible must leave that log EMPTY. Also pins S-P13: no OAuth button, no deep links.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

JOURNEY = """
import json, sys
sys.path.insert(0, {repo!r})
from core.runtime_paths import configure_runtime_home
import storage.db as sdb

configure_runtime_home({home!r})
sdb.configure_default_db_path({home!r} + "/data/vool_web0_v2.db")

from apps.vool_api_server import _bootstrap
from core.web.api.service import dispatch_get, dispatch_post

runtime = _bootstrap(run_prewarm=False)

def get(path):
    r = dispatch_get(path=path, query={{}}, runtime=runtime, model_name="vool", client_host="127.0.0.1")
    return r.status

def post(path, body):
    r = dispatch_post(path=path, body=body, headers={{"Content-Type": "application/json"}}, runtime=runtime,
                      model_name="vool", workspace_root_provider=lambda: {home!r} + "/workspace", client_host="127.0.0.1")
    if r.stream is not None:
        for _ in r.stream:
            pass
    return r.status

statuses = {{}}
statuses["pact_get"] = get("/api/onboarding/pact")
statuses["commands_get"] = get("/api/commands")
statuses["begin"] = post("/api/onboarding/pact/begin", {{}})
snap_status, _ = get("/api/onboarding/pact"), None
statuses["advance_naming"] = post("/api/onboarding/pact/advance", {{"to": "naming"}})
statuses["name"] = post("/api/onboarding/pact/name", {{"keep_default": True, "preferred_address": "Alex"}})
statuses["facts"] = post("/api/onboarding/pact/facts", {{"items": [{{"category": "locale", "value": "lt-LT"}}]}})
statuses["boundary"] = post("/api/onboarding/pact/boundary", {{"key": "local_only_composite", "value": True}})
statuses["choice"] = post("/api/onboarding/pact/choice/local-only", {{}})
statuses["skip"] = post("/api/onboarding/pact/skip", {{}})
print("JOURNEY_STATUSES:" + json.dumps(statuses))
"""


def test_the_full_pact_tour_with_the_card_visible_makes_zero_credential_calls(tmp_path):
    from tests._credential_probe import credential_calls, fake_bin_dir, read_log

    home = tmp_path / "popup-home"
    home.mkdir(parents=True)
    log = tmp_path / "c15.log"
    bin_dir = fake_bin_dir(root=tmp_path / "fakebins", python=sys.executable, log=log)

    import os

    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '/usr/bin:/bin')}"
    env["C15_FAKE_BIN_LOG"] = str(log)
    env["VOOL_HOME"] = str(home)
    env["VOOL_KEY_STORAGE_MODE"] = "file"
    env["VOOL_CREDENTIAL_STORE"] = "vault"
    env.pop("VOOL_KEYCHAIN_ALLOWED", None)
    env["VOOL_DISABLE_MESH_DAEMON"] = "1"
    env["VOOL_SKIP_PROVIDER_PREWARM"] = "1"
    env["VOOL_PUBLIC_HIVE_ENABLED"] = "0"

    script = tmp_path / "journey.py"
    script.write_text(JOURNEY.format(repo=str(REPO_ROOT), home=str(home)))
    proc = subprocess.run([sys.executable, str(script)], env=env, capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stderr[-600:]
    line = next(l for l in proc.stdout.splitlines() if l.startswith("JOURNEY_STATUSES:"))
    statuses = json.loads(line[len("JOURNEY_STATUSES:"):])
    unexpected = {k: v for k, v in statuses.items() if v not in (200, 409)}
    assert not unexpected, unexpected

    # THE fence: the fake credential binaries recorded nothing at all.
    assert credential_calls(log) == []


def test_the_setup_surfaces_carry_no_oauth_and_no_browser_deep_links():
    """The pact card left the chat page (2026-09-17); its successor surfaces keep the same fence:
    the chat's one setup line and the guided /setup page open nothing outward and start no OAuth.

    The /setup page now also ships the i18n bootstrap (a pure DATA script: it defines the
    deterministic VOOLT lookup and carries the message catalog). Provider-error explanations
    inside that data legitimately mention the standard HTTP phrase "401 Unauthorized" — that is
    not an OAuth endpoint, so the fence scans the page WITHOUT the inert data script while
    still scanning every executable line, attribute and URL the page carries."""
    from core.vool_chat_page import render_vool_chat_html
    from core.vool_setup_page import render_vool_setup_html

    chat = render_vool_chat_html()
    assert 'id="pactCard"' not in chat and "PACT_COPY" not in chat
    start = chat.index('id="setupLine"')
    region = chat[start:chat.index("</div>", start)]
    setup = render_vool_setup_html()
    setup_scan = re.sub(
        r'<script id="vool-i18n">.*?</script>', "", setup, count=1, flags=re.DOTALL
    )
    for banned in ("oauth", "authorize", "vool://", "vool://"):
        assert banned not in region.lower()
        assert banned not in setup_scan.lower()
    # The page's only window.open targets are this runtime's own Settings surface.
    for hit in re.findall(r"window\.open\(([^)]*)\)", setup):
        assert "/settings" in hit, hit
