"""The "Save this campaign?" card under a social content manager answer.

A person who plans a campaign in chat can keep it: the card under the answer shows the goal, who
it is for and where, prefilled from their own ask, and nothing is saved until they press Save.
Save sends exactly what the card shows to campaign.create as the person. The AI has no way to
save: campaign.create stays operator-only and refuses a model principal. The pure helpers run
under node; the page wiring and the catalogs are read from the shipped files.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PAGE = (REPO / "core" / "vool_chat_page.py").read_text(encoding="utf-8")
NODE = shutil.which("node")
CARD_KEYS = (
    "title", "note", "goal", "audience", "platforms", "save", "dismiss", "need", "saved", "failed",
)


def _run(call: str):
    start = PAGE.index("const CAMPAIGN_SKILL_TITLE = ")
    end = PAGE.index("function paintCampaignCard(", start)
    script = PAGE[start:end] + f"\nprocess.stdout.write(JSON.stringify({call}));\n"
    proc = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_card_starts_from_the_persons_own_ask() -> None:
    ask = "Plan a launch campaign for our privacy tool across X and LinkedIn"
    draft = _run(f"campaignDraftFromAsk({json.dumps(ask)})")
    assert draft == {"objective": ask, "audience": "", "platforms": ["x", "linkedin"]}
    other = _run('campaignDraftFromAsk("content calendar for bluesky n mastodon, 2x a week")')
    assert other["platforms"] == ["bluesky", "mastodon"]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_save_sends_exactly_what_the_card_shows() -> None:
    fields = {"objective": " Explain receipts ", "audience": "privacy-minded builders",
              "platforms": "LinkedIn, bluesky;  x"}
    req = _run(f"campaignCreateRequest({json.dumps(fields)})")
    assert req == {
        "command_id": "campaign.create",
        "input": {"project_id": "my-campaigns", "objective": "Explain receipts",
                  "audience": "privacy-minded builders", "platforms": ["linkedin", "bluesky", "x"],
                  "guardrails": []},
    }


def test_the_card_is_painted_only_under_a_social_content_manager_answer() -> None:
    body = PAGE[PAGE.index("async function noteSkillsUsed(run) {"):PAGE.index("const CAMPAIGN_SKILL_TITLE = ")]
    assert "if (names.indexOf(CAMPAIGN_SKILL_TITLE) >= 0) {" in body
    assert body.count("paintCampaignCard(") == 1
    assert "const CAMPAIGN_SKILL_TITLE = 'Social content manager';" in PAGE
    # The only write is behind the Save button's click handler; painting the card saves nothing.
    paint = PAGE[PAGE.index("function paintCampaignCard("):]
    paint = paint[:paint.index("\n}\n")]
    assert paint.count("/api/commands/dispatch") == 1
    click = paint.index("save.addEventListener('click'")
    assert paint.index("/api/commands/dispatch") > click


def test_every_card_word_is_in_every_catalog() -> None:
    used = set(re.findall(r"pageT\('(chat\.campaign_card_[a-z_]+)'", PAGE))
    assert used == {f"chat.campaign_card_{k}" for k in CARD_KEYS}
    en = json.loads((REPO / "core/i18n/catalogs/en.json").read_text(encoding="utf-8"))["messages"]
    for path in sorted((REPO / "core/i18n/catalogs").glob("*.json")):
        messages = json.loads(path.read_text(encoding="utf-8"))["messages"]
        for key in used:
            assert messages.get(key), (path.name, key)
            if path.stem != "en":
                assert messages[key] != en[key], (path.name, key)


def test_the_ai_still_cannot_save_a_campaign(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "home"))
    from core.command_registry.execute import ExecutionContext, execute_command

    payload = {"project_id": "my-campaigns", "objective": "o", "audience": "a", "platforms": ["x"]}
    env = execute_command("campaign.create", payload,
                          context=ExecutionContext(projection="model", principal="model"))
    assert not env.ok and env.fault.code == "permission_required"
    listed = execute_command("campaign.list", {"project_id": "my-campaigns"},
                             context=ExecutionContext(projection="model", principal="model"))
    assert listed.ok and listed.data["count"] == 0
    saved = execute_command("campaign.create", payload,
                            context=ExecutionContext(projection="api", principal="operator"))
    assert saved.ok
