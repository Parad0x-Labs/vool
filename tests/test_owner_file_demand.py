"""The owner's own file asks no lane owns reach the model WITH the machine file tools, owner turns only.

Measured 2026-10-27 with the tool-seating offer probe: "where is my resume", "send day4.jpg to my
phone", "copy day4.jpg to my desktop" and 13 more such asks reached a pinned model with no tool list
at all (0 of 16 offered a file tool), because the requirements read them as plain chat.
"""
from __future__ import annotations

import pytest

from core.execution_requirements import owner_file_demand_allowed, requirements_for
from core.request_trust import OWNER_LOCAL_KEY
from core.tool_demand_signals import owner_file_demand, owner_file_intents

OWNER = {OWNER_LOCAL_KEY: True}
REMOTE = {OWNER_LOCAL_KEY: False, "surface": "mobile_companion", "remote_device_id": "dev-1"}

ASKS = [
    "where is my resume", "open my downloads folder", "copy day4.jpg to my desktop",
    "search my documents for the word milk", "find the invoice from last week", "which apps are installed",
    "send day4.jpg to my phone", "can you see my files?", "do you have access to my computer?",
    "I need the picture from yesterday's meeting, it's somewhere in my downloads",
    # Fresh asks, never tuned against.
    "where's that contract I signed last month", "grab the screenshot I took this morning",
    "is my tax return pdf somewhere on my computer", "what's taking up space on my disk",
    "read the readme.md on my desktop", "how many photos do I have", "do I still have the wedding video",
]
PROSE = [
    "what is the capital of France", "write a poem about my mac", "how do I find large files on a mac",
    "explain how pdf compression works", "what's the best photo editor", "I took a photo of the sunset",
    "tell me a story about a lost file", "my computer is slow, why", "what does a resume need to include",
    "is my laptop worth upgrading", "what is my mac address", "which mac should I buy", "what os should my laptop run",
    "what's the best way to back up my photos", "how do I list pictures in downloads in python", "open google.com",
]


@pytest.mark.parametrize("text", ASKS)
def test_an_ask_about_the_owners_files_is_a_file_demand(text):
    assert owner_file_demand(text)


@pytest.mark.parametrize("text", PROSE)
def test_prose_about_files_or_computers_is_not(text):
    assert not owner_file_demand(text)


def test_the_seated_tools_fit_the_ask():
    assert owner_file_intents("where is my resume")[0] == "machine.find_file"
    assert owner_file_intents("help me free up space on this mac")[0] == "machine.find_largest"


@pytest.mark.parametrize("context", [None, {}, REMOTE, {"surface": "telegram", "_http_ingress": True}])
def test_a_remote_or_unknown_turn_gets_no_file_tools(context):
    assert not owner_file_demand_allowed("where is my resume", context)
    reading = requirements_for("where is my resume", source_context=context)
    assert reading.tools_required is False and "filesystem" not in reading.allowed_toolsets


def test_a_phone_with_the_computer_grant_gets_them(monkeypatch):
    import core.request_trust as request_trust

    monkeypatch.setattr(request_trust, "remote_turn_has_grant", lambda ctx, grant: grant == request_trust.REMOTE_GRANT_COMPUTER)
    assert owner_file_demand_allowed("where is my resume", REMOTE)


def test_the_owner_gets_the_file_tools_for_a_plain_file_ask():
    reading = requirements_for("where is my resume", source_context=OWNER)
    assert reading.tools_required is True and reading.allowed_toolsets == ("filesystem",)
    assert "owner_file_request" in reading.reason_codes
    chat = requirements_for("what is the capital of france", source_context=OWNER)
    assert chat.tools_required is False and chat.allowed_toolsets == ()


@pytest.mark.parametrize("text", [
    "what's the latest news about apple and is my resume pdf in my downloads",
    "what's the bitcoin price right now, and find my invoice pdf in downloads",
    "search the web for the best laptops and save the list in my documents",
])
def test_the_file_tools_only_add_to_a_live_or_grounded_reading(text):
    remote = requirements_for(text, source_context=REMOTE)
    owner = requirements_for(text, source_context=OWNER)
    assert owner.answer_mode == remote.answer_mode
    assert owner.current_information_required == remote.current_information_required
    assert owner.tools_required >= remote.tools_required
    assert set(remote.allowed_toolsets) <= set(owner.allowed_toolsets)
    assert set(owner.allowed_toolsets) - set(remote.allowed_toolsets) <= {"filesystem"}


def test_the_model_is_offered_find_file_on_an_owner_turn_only():
    from core.capability_graph import model_visible_specs

    def offered(context):
        reading = requirements_for("where is my resume", source_context=context)
        specs = model_visible_specs(
            user_text="where is my resume", toolset_hints=reading.allowed_toolsets, source_context=context,
        )
        return [getattr(spec, "intent", None) or spec.get("intent") for spec in specs]

    assert "machine.find_file" in offered(dict(OWNER))
    assert "machine.find_file" not in offered(dict(REMOTE))


def test_machine_info_asks_seat_the_specs_tool():
    assert owner_file_intents("which apps are installed")[0] == "machine.inspect_specs"
    assert owner_file_intents("what's my computer's name and macOS version")[0] == "machine.inspect_specs"


@pytest.mark.parametrize(("text", "brief"), [
    ("I need the picture from yesterday's meeting, it's somewhere in my downloads", False),
    ("give me the photos from my downloads folder, 5 days old at most", False),
    ("give me the video I recorded yesterday", False),
    ("give me a picture of a cat on the moon", True),
    ("make me an image of my dog surfing", True),
    ("write an image prompt for my desktop wallpaper", True),
])
def test_a_picture_the_owner_has_is_a_file_not_an_image_prompt(text, brief):
    from core.creative_director import detect_creative_brief

    assert (detect_creative_brief(text) is not None) is brief


@pytest.mark.parametrize(("text", "generates"), [
    ("give me the photos from my downloads folder, 5 days old at most", False),
    ("give me the picture day4 from downloads", False),
    ("give me a photo of a cat", True),
    ("give me a picture of my dog", True),
    ("generate an image of my downloads folder as a cartoon", True),
])
def test_fetching_the_owners_pictures_is_not_making_one(text, generates):
    from core.execution.constants import image_generation_intent

    assert (image_generation_intent(text) is not None) is generates
