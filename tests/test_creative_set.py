from __future__ import annotations

import core.creative_set as cs
from core.creative_set import Character, CreativeSet, compose_scene_prompt, set_from_description

_SET = CreativeSet(
    name="SET1",
    characters=(Character(
        name="Aria",
        appearance="a young sorceress, silver-streaked black hair, sharp green eyes, pale scar on left cheek",
        wardrobe="a torn charcoal cloak over dark leather",
        voice="low, resolute, faintly hoarse",
    ),),
    setting="a ruined marble bridge over a storm valley",
    background="churning black clouds, distant violet lightning",
    style="dark fantasy, painterly realism", palette="slate, bone, glacial cyan", aspect="21:9",
    mood="ominous, myth-weight", story="she crossed the dead kingdom and will not kneel",
    negative="modern clothing",
)


def test_roundtrip_serialization() -> None:
    assert CreativeSet.from_dict(_SET.to_dict()) == _SET


def test_compose_video_locks_character_world_and_script() -> None:
    p = compose_scene_prompt(_SET, "she raises the cracked staff as runes ignite", medium="video")
    assert "Aria" in p
    assert "silver-streaked black hair" in p and "torn charcoal cloak" in p   # locked look + wardrobe
    assert "low, resolute" in p                                                # locked voice
    assert "ruined marble bridge" in p and "churning black clouds" in p        # locked world
    assert "she raises the cracked staff" in p                                 # this shot's script
    assert "21:9" in p and "glacial cyan" in p                                 # locked style/palette
    assert "EXACTLY consistent" in p and "off-model" in p                      # continuity enforcement
    assert "modern clothing" in p                                              # set negative folded in


def test_compose_image_variant() -> None:
    p = compose_scene_prompt(_SET, "a close portrait, rain on her face", medium="image")
    assert "identical to the established set" in p
    assert "silver-streaked black hair" in p
    assert "Negative prompt:" in p


def test_compose_handles_minimal_set() -> None:
    p = compose_scene_prompt(CreativeSet(name="bare"), "a robot walks", medium="video")
    assert "the established character(s)" in p and "a robot walks" in p


def test_norm_is_case_and_space_insensitive() -> None:
    assert cs._norm("  SET 1 ") == "set 1"
    assert cs._norm("SET1") == cs._norm("set1")


def test_persist_roundtrip(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cs, "_sets_path", lambda: tmp_path / "sets.json")
    assert cs.get_set("SET1") is None
    cs.save_set(_SET)
    got = cs.get_set("set1")                        # case-insensitive lookup
    assert got is not None and got.name == "SET1"
    assert got.characters[0].appearance == _SET.characters[0].appearance
    assert cs.list_set_names() == ["SET1"]
    assert cs.delete_set("SET1") is True
    assert cs.get_set("SET1") is None
    assert cs.delete_set("nope") is False


def test_load_sets_empty_when_no_file(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cs, "_sets_path", lambda: tmp_path / "none.json")
    assert cs.load_sets() == {}
    assert cs.list_set_names() == []


# --- natural-language "create a set from this paragraph" ---

def test_set_from_prose_paragraph() -> None:
    s = set_from_description(
        "SET1: Aria is a tall woman in her thirties with silver hair and green eyes, wearing a black "
        "trench coat. She has a calm, low voice. The scene is a rain-soaked neon Tokyo alley at night. "
        "Cinematic and moody, teal-and-magenta palette. Aspect 21:9. She's a rogue courier hunting a "
        "stolen data-shard."
    )
    assert s.name == "SET1"                               # "SET1:" prefix becomes the set name
    c = s.characters[0]
    assert c.name == "Aria"
    assert "silver hair" in c.appearance and "wearing" not in c.appearance
    assert c.wardrobe == "black trench coat"             # pulled from "wearing ..."
    assert c.voice == "calm, low"                        # pulled from "... voice"
    assert s.aspect == "21:9" and s.palette == "teal-and-magenta"
    assert "cinematic" in s.style and s.mood == "moody"
    assert "Tokyo alley" in s.setting and "rogue courier" in s.story


def test_set_from_labeled_fields_are_authoritative() -> None:
    s = set_from_description(
        "Name: Nightfall. Character: Kael, a grizzled knight with a scarred jaw. "
        "Wardrobe: dented steel armor. Voice: deep and gravelly. Setting: a burning cathedral. "
        "Style: dark fantasy, painterly. Palette: ember orange and ash grey. Aspect: 2.39:1. "
        "Story: he guards the last relic."
    )
    assert s.name == "Nightfall" and s.aspect == "2.39:1"   # decimal cinema ratio preserved
    c = s.characters[0]
    assert c.name == "Kael" and c.appearance == "a grizzled knight with a scarred jaw"
    assert c.wardrobe == "dented steel armor" and c.voice == "deep and gravelly"
    assert s.setting == "a burning cathedral" and s.story == "he guards the last relic"
    assert s.palette == "ember orange and ash grey"


def test_set_from_description_name_arg_wins_over_prefix() -> None:
    s = set_from_description("SET1: a lone android in a chrome corridor", name="MyOverride")
    assert s.name == "MyOverride"


def test_set_from_description_empty_is_safe() -> None:
    s = set_from_description("")
    assert s.name == "Untitled set" and s.characters == () and s.aspect == "16:9"
    s2 = set_from_description("just some vibes with no structure", name="V")
    assert s2.name == "V"                                  # never raises, keeps the given name


def test_set_from_description_composes_into_a_prompt() -> None:
    s = set_from_description(
        "Character: Mara, a freckled pilot with cropped red hair. Setting: a cramped cockpit. "
        "Style: gritty sci-fi."
    )
    prompt = compose_scene_prompt(s, "she flips the ignition", medium="video")
    assert "Mara" in prompt and "cropped red hair" in prompt and "she flips the ignition" in prompt


# --- regressions from the adversarial review of the NL parser ---

def test_long_prose_without_palette_is_linear_time() -> None:
    import time
    text = "a soft warm gentle glow " * 1500        # ~36 KB, no "palette" word
    start = time.perf_counter()
    s = set_from_description(text)
    elapsed = time.perf_counter() - start
    assert s.palette == ""
    assert elapsed < 2.0                             # was O(n^2): ~20s pre-fix, guards against ReDoS


def test_clock_time_is_not_read_as_aspect_ratio() -> None:
    assert set_from_description("Meet at 3:30 in a dark room.").aspect == "16:9"
    assert set_from_description("Deadline is 5:45 sharp.").aspect == "16:9"
    assert set_from_description("meeting at 6:30, then a 21:9 hero shot").aspect == "21:9"  # real ratio still wins
    assert set_from_description("Cinematic 2.39:1 look.").aspect == "2.39:1"


def test_two_named_characters_do_not_form_a_chimera() -> None:
    s = set_from_description(
        "Kael is a grizzled knight with a scarred jaw, wearing dented steel armor. "
        "Lyra is a young mage with silver hair and violet eyes, dressed in a midnight-blue robe. "
        "They stand in a burning cathedral. Epic dark fantasy."
    )
    c = s.characters[0]
    assert c.name == "Kael"
    assert "grizzled knight" in c.appearance and "Lyra" not in c.appearance   # own face, not Lyra's
    assert c.wardrobe == "dented steel armor"


def test_palette_does_not_spill_across_bang_or_question_terminators() -> None:
    s = set_from_description("A palette of gold and crimson! She is a knight who hunts dragons.")
    assert s.palette == "gold and crimson"


def test_wardrobe_stops_at_an_action_verb() -> None:
    assert set_from_description("A samurai in a silk kimono walks the market").characters[0].wardrobe == "silk kimono"
    assert set_from_description(
        "Aria wearing a red cloak stands beside Ben wearing a blue coat").characters[0].wardrobe == "red cloak"
    # a legitimately long, verb-free wardrobe phrase is preserved
    kept = set_from_description("Wardrobe: a red cloak trimmed with gold and a leather belt.")
    assert kept.characters[0].wardrobe == "a red cloak trimmed with gold and a leather belt"


def test_voice_reads_descriptor_after_voice_is() -> None:
    assert set_from_description("Elena is a woman. Her voice is soft and melodic.").characters[0].voice == "soft and melodic"
    assert set_from_description("Marcus is a man. His voice is deep and gravelly.").characters[0].voice == "deep and gravelly"
    assert set_from_description("She has a calm, low voice.").characters[0].voice == "calm, low"  # pre-modifier still works
