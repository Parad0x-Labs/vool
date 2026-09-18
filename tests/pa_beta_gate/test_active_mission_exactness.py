"""pa_beta_gate — active-mission exact-literal preservation through the REAL path.

Codex Phase 2 F1: the input normalizer split exact mission values ("0.037" ->
"0. 037", "alice.null" -> "alice. null", paths on / and \\, and it mutated
"project" -> "protect" via fuzzy domain match). The PA gate had missed this because
its exactness tests inserted directly into L3, bypassing normalize_user_text.

These tests drive the REAL input path — normalize_user_text and the dialogue goal
(adapt_user_input -> current_user_goal) — and assert every mission literal survives
character-for-character.
"""
from __future__ import annotations

import uuid

import pytest

from core.human_input_adapter import adapt_user_input
from core.input_normalizer import normalize_user_text
from storage.dialogue_memory import get_dialogue_session

pytestmark = [pytest.mark.pa_beta]


# (natural sentence containing the literal, the exact substring that must survive)
MISSION_LITERALS = [
    ("My launch spend cap is exactly 0.037 SOL and must not be exceeded.", "0.037"),
    ("Set slippage to 0.5 percent and gas budget 0.0021 SOL.", "0.0021"),
    ("Pay 1.2345 USDC for the compute lease.", "1.2345"),
    ("Register alice.null as the project domain.", "alice.null"),
    ("My handle is sls-0x.null and the docs live at goldenloop.null.", "goldenloop.null"),
    ("Use the nested name dev.team.null for staging.", "dev.team.null"),
    ("The mainnet launch deadline is 2026-07-15, plan around it.", "2026-07-15"),
    ("The window opens 2026-07-09 14:30 sharp.", "2026-07-09"),
    ("Ship at exactly 14:30 today.", "14:30"),
    ("The spend policy lives at data/keys/spend_policy.json on disk.", "data/keys/spend_policy.json"),
    ("Logs are written to /mnt/data/project/runtime.log every run.", "/mnt/data/project/runtime.log"),
    ("Run the installer/bootstrap_vool.ps1 script first.", "installer/bootstrap_vool.ps1"),
    ("The config file is settings.toml in the root.", "settings.toml"),
    ("My launch wallet prefix is F6Fr2 and only that one.", "F6Fr2"),
    ("Use the treasury starting 28hxX for payouts.", "28hxX"),
    ("My operator node id is 8829145, keep it.", "8829145"),
    ("The staging API port is 8096 and prod is 8097.", "8096"),
    ("Keep working on my project and connect the correct object.", "project"),
]

WINDOWS_PATH = (r"The wallet lives at C:\Users\test\Documents\vool on this box.", r"C:\Users\test\Documents\vool")


@pytest.mark.parametrize("sentence,needle", MISSION_LITERALS, ids=lambda v: v if isinstance(v, str) and len(v) < 30 else "")
def test_literal_survives_normalization(sentence, needle):
    out = normalize_user_text(sentence).normalized_text
    assert needle in out, f"normalizer lost {needle!r}: {out!r}"


def test_windows_path_survives_normalization():
    out = normalize_user_text(WINDOWS_PATH[0]).normalized_text
    assert WINDOWS_PATH[1] in out, out


def test_grouped_monetary_values_survive_normalization_character_for_character():
    prompt = "I have 3,000 CRC and spend 1,500 CRC. The item costs 1,234.56 CRC."
    out = normalize_user_text(prompt).normalized_text
    assert out == prompt


@pytest.mark.parametrize("sentence,needle", MISSION_LITERALS, ids=lambda v: v if isinstance(v, str) and len(v) < 30 else "")
def test_literal_survives_into_dialogue_goal(sentence, needle):
    sid = f"openclaw:exact:{uuid.uuid4().hex}"
    adapt_user_input(sentence, session_id=sid)
    goal = str(get_dialogue_session(sid).get("current_user_goal") or "")
    assert needle in goal, f"dialogue goal lost {needle!r}: {goal!r}"


def test_project_is_not_mutated_to_protect():
    # the fuzzy domain match used to rewrite "project" -> "protect"
    out = normalize_user_text("help me finish my project this week").normalized_text
    assert "project" in out and "protect" not in out
