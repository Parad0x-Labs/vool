"""Answer-quality lines on v0.8: each turn reaches the lane that owns it (2026-11-12).

Measured in-process and in served project chats on the v0.8 candidate, every first case below went to
the wrong lane: a question naming a file in the chat's project got "I don't have that saved" from
cross-chat recall, the to-do finder ("I couldn't find a to-do list file in Desktop or Documents") or
the computer-wide file search ("None of the searchable local folders are readable"); "show me my
notes", "what's in my notes?" and "Find my chat about ..." were rewritten by recall into "what's my
...?" and answered "I don't have that saved"; pasted text whose words sound live ("the weather today
was dry") and a question about a document the chat keeps were refused or labelled as written from
memory because Web lookups were off; and a paste that opens "Here is the plan ... Read it, then
answer my questions" or "Below is the draft contract." was split into tasks and searched for on
disk; and "read notes.txt" or "Read tally.csv" was refused as a web lookup because the file name
looked like a web address. The measured wordings come first in each group, then new ones.
Contributor: sls_0x.
"""
from __future__ import annotations

import pytest

from apps.vool_agent import VoolAgent
from core import chat_attachments, runtime_paths
from core.inline_payload import turn_supplies_its_own_content

MODEL_REACHED = {"selected_model_blocked", "model_unavailable", "no_provider_available"}
WRONG_LANE_REPLIES = ("i don't have that saved", "couldn't find a to-do list",
                      "searchable local folders", "web lookups are off")


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "home"))
    runtime_paths.configure_runtime_home(tmp_path / "home")
    yield
    runtime_paths.configure_runtime_home(None)


def _turn(text, *, session, files=None, workspace=None, kept_document=None):
    context = {"surface": "api", "_owner_local": True, "runtime_session_id": session,
               "operating_mode": "auto", "requested_model": "unavailable:quality-lines"}
    if files is not None:
        workspace.mkdir()
        for name, body in files.items():
            (workspace / name).write_text(body, encoding="utf-8")
        context.update({"workspace": str(workspace), "workspace_binding": "project",
                        "project_id": "quality-lines-project"})
    if kept_document is not None:
        doc = chat_attachments.stage_document(session_id=session, data=kept_document.encode())
        chat_attachments.bind_to_turn(session_id=session, turn_id="source", attachment_ids=[doc["id"]])
        chat_attachments.release_turn(session_id=session, turn_id="source", outcomes=None)
    result = VoolAgent(backend_name="test-backend", device="quality-lines").run_once(
        text, session_id_override=session, source_context=context,
    )
    reads = [str(item.get("path") or "") for item in context.get("runtime_tool_observations") or []
             if item.get("intent") == "workspace.read_file"]
    return result, str(result.get("response") or ""), reads


# --- a question that names a file in the chat's project reads that file ----------------------


@pytest.mark.parametrize("question,files,named,content", [
    ("What do my dentist notes say?", {"dentist_notes.md": "Dentist on Friday 10:30 at Smile Clinic.\n",
                                       "gym.md": "Leg day Monday.\n"}, "dentist_notes.md", "Smile Clinic"),
    ("whats in my dentist notes", {"dentist_notes.md": "Dentist on Friday 10:30 at Smile Clinic.\n",
                                   "gym.md": "Leg day Monday.\n"}, "dentist_notes.md", "Smile Clinic"),
    ("whats in my moving checklist?", {"moving_checklist.md": "Book the van for 3 May.\nCancel the gym.\n"},
     "moving_checklist.md", "Book the van"),
    ("What were the total expenses in expenses_2026_q3.csv?",
     {"expenses_2026_q3.csv": "item,amount\nrent,1450\ntravel,312.40\nfood,280.10\n", "notes.md": "misc\n"},
     "expenses_2026_q3.csv", "312.40"),
    ("how much did we spend on travel per the Q3_Expenses.CSV file?",
     {"Q3_Expenses.CSV": "item,amount\nrent,1450\ntravel,312.40\n", "readme.md": "x\n"}, "Q3_Expenses.CSV", "312.40"),
    ("tell me what my renovation notes say about the boiler",
     {"renovation_notes.md": "Boiler swap booked for 9 April.\n", "paint.md": "Sage green.\n"},
     "renovation_notes.md", "9 April"),
    ("does my packing checklist have the tent on it?", {"packing_checklist.md": "- tent\n- stove\n"},
     "packing_checklist.md", "stove"),
    ("what's the rent line in budget_2027.csv", {"budget_2027.csv": "item,amount\nrent,1500\npower,90\n"},
     "budget_2027.csv", "1500"),
])
def test_a_question_naming_a_project_file_reads_that_file(tmp_path, question, files, named, content):
    _, response, reads = _turn(question, session="openclaw:6234567890abcdef0001", files=files,
                               workspace=tmp_path / "workspace")
    assert named in reads or content in response, (reads, response)
    assert not any(reply in response.lower() for reply in WRONG_LANE_REPLIES), response


# --- notes and saved-chat asks are not memory recall --------------------------------------------


@pytest.mark.parametrize("text", [
    "show me my notes", "what's in my notes?", "Find my chat about ceramic calibration",
    "open my journal", "what did I write in my diary about Lisbon?", "show me my meeting notes",
    "list my notes", "what's in my notebook", "find the conversation where we talked about kiln temperatures",
])
def test_notes_and_saved_chat_asks_are_not_recall(text):
    from core.agent_runtime.cross_chat_recall import _owned_by_another_lane, as_question

    assert _owned_by_another_lane(as_question(text), text), as_question(text)


@pytest.mark.parametrize("text", [
    "where is my test parking spot at work", "what's my gate code?", "when is my flight to Lisbon?",
    "what's my sister's birthday", "which bakery do i like most", "what did i say my wifi password was",
])
def test_the_owners_own_facts_are_still_recall(text):
    from core.agent_runtime.cross_chat_recall import _owned_by_another_lane, as_question

    assert not _owned_by_another_lane(as_question(text), text), as_question(text)


@pytest.mark.parametrize("text", ["what's in my notes?", "show me my notes"])
def test_notes_in_a_project_chat_never_get_i_dont_have_that_saved(tmp_path, text):
    _, response, _ = _turn(text, session="openclaw:6234567890abcdef0002",
                           files={"notes.md": "Call the bank on Monday.\n"}, workspace=tmp_path / "workspace")
    assert "i don't have that saved" not in response.lower(), response


@pytest.mark.parametrize("text", ["Find my chat about ceramic calibration", "find my chat about the boiler service"])
def test_find_my_chat_is_the_saved_chat_search(text):
    result, response, _ = _turn(text, session="openclaw:6234567890abcdef0003")
    assert "i don't have that saved" not in response.lower(), response
    assert "couldn't find" in response.lower() or "housekeeping" in str(result.get("source_context", {})).lower(), response


# --- web lookups off: text the person gave is answered from that text --------------------------


QUARRY = "The quarry team is led by supervisor Q7. It assembles in the west depot.\n" * 90


@pytest.mark.parametrize("text", [
    "Summarize the following:\nField log, week 41. The weather today was dry and windy, prices at the depot went "
    "up 4%, and the news crew filmed the north wall. Crew B finished the drainage trench.",
    "Here are my notes from the site visit. What are the open issues?\n\nThe weather today was wet. The crane "
    "price went up again. The latest news from the council: the permit is still pending.",
])
def test_pasted_text_that_sounds_live_reaches_the_model(text):
    result, response, _ = _turn(text, session="openclaw:6234567890abcdef0004")
    assert "web lookups are off" not in response.lower(), response
    assert result.get("model_execution", {}).get("source") in MODEL_REACHED, result


@pytest.mark.parametrize("question", [
    "Who leads the quarry team, and where does it assemble indoors?",
    "which depot does the quarry team use these days, and who is its supervisor now?",
])
def test_a_question_about_a_kept_document_is_not_labelled_as_memory(question):
    result, response, _ = _turn(question, session="openclaw:6234567890abcdef0005", kept_document=QUARRY)
    assert "web lookups are off" not in response.lower(), response
    assert result.get("model_execution", {}).get("source") in MODEL_REACHED, result


@pytest.mark.parametrize("question", ["what's the weather in Paris right now?", "what's the weather today?",
                                      "any news about the quarry strike today?"])
def test_a_live_question_with_a_kept_document_still_says_web_lookups_are_off(question):
    result, response, _ = _turn(question, session="openclaw:6234567890abcdef0006", kept_document=QUARRY)
    assert "web lookups are" in response.lower() and "settings" in response.lower(), response
    assert result.get("model_execution", {}).get("source") not in MODEL_REACHED, result


# --- a paste opened by a lead-in is supplied material -----------------------------------------


NOTES = "\n\n" + "The vendor review for site 4 slipped a week; Dana follows up with procurement.\n" * 3


@pytest.mark.parametrize("text", [
    "Here is the plan for our neighbourhood repair cafe. Read it, then answer my questions one at a time. For now "
    "just tell me in two sentences what it is about.\n\n# Riverside Repair Cafe\nWe meet monthly.",
    "Here are my meeting notes from Tuesday. Go through them and tell me the three action items:" + NOTES,
    "Below is the draft contract. Can you point out anything unusual?" + NOTES,
    "OK here's the agenda for Monday. What's missing?\n\n1. Budget review\n2. Hiring\n3. Office move",
    "Here is my essay draft. Is the opening too long?\n\nWhen I moved to Riga I knew nobody.",
    "Hi, below are the minutes from the board meeting. Summarise the decisions." + NOTES,
])
def test_a_lead_in_hands_over_the_material_that_follows(text):
    assert turn_supplies_its_own_content(text) is True


@pytest.mark.parametrize("text", [
    "Here's the contract, save it to my documents." + NOTES,
    "Here is the plan. Email it to Dana." + NOTES,
    "Here is the file:\n/Users/amy/Documents/notes.txt",
    "Here's the weather for tomorrow?",
    "Here is my question: what is a mutex?",
])
def test_a_lead_in_that_asks_for_an_action_or_points_elsewhere_keeps_its_owner(text):
    assert turn_supplies_its_own_content(text) is False


def test_the_measured_meeting_notes_paste_is_not_searched_for_on_disk():
    result, response, _ = _turn("Here are my meeting notes from Tuesday. Go through them and tell me the three "
                                "action items:" + NOTES * 10, session="openclaw:6234567890abcdef0007")
    assert "searchable local folders" not in response.lower(), response
    assert result.get("model_execution", {}).get("source") in MODEL_REACHED, result


# --- a file name is not a web address -----------------------------------------------------------


@pytest.mark.parametrize("text", ["read notes.txt", "Read tally.csv", "summarize report.pdf",
                                  "read my budget.xlsx", "summarize the minutes.docx file"])
def test_a_file_name_is_not_a_web_lookup(text):
    from core.task_router import looks_like_explicit_lookup_request

    assert looks_like_explicit_lookup_request(text) is False


@pytest.mark.parametrize("text", ["read example.com and tell me what it says", "summarize bbc.co.uk",
                                  "summarize the bbc.com article", "read https://example.org/post"])
def test_a_site_is_still_a_web_lookup(text):
    from core.task_router import looks_like_explicit_lookup_request

    assert looks_like_explicit_lookup_request(text) is True


@pytest.mark.parametrize("text,files,shown", [
    ("Read tally.csv", {"tally.csv": "label,value\nunit-a,12.75\n"}, "unit-a,12.75"),
    ("read notes.txt", {"notes.txt": "Pick up the parcel.\n"}, "Pick up the parcel"),
    ("open budget.tsv", {"budget.tsv": "item\tamount\nrent\t1500\n"}, "1500"),
])
def test_reading_a_project_file_is_not_refused_as_a_web_lookup(tmp_path, text, files, shown):
    _, response, _ = _turn(text, session="openclaw:6234567890abcdef0008", files=files,
                           workspace=tmp_path / "workspace")
    assert "web lookups are off" not in response.lower(), response
    assert shown in response, response


def test_reading_a_file_in_a_plain_chat_is_not_refused_as_a_web_lookup():
    _, response, _ = _turn("read notes.txt", session="openclaw:6234567890abcdef0009")
    assert "web lookups are off" not in response.lower(), response


@pytest.mark.parametrize("text", [
    "How many tonnes does the yard currently hold according to my notes?",
    "what was the boiler service date according to my notes?",
    "based on my notes, when is the gas meter reading due?",
])
def test_a_fact_asked_according_to_my_notes_is_still_a_records_question(text):
    # The served records test (tests/test_live_question_is_not_answered_from_the_records.py) pins it:
    # this lane says plainly that nothing is saved; the notes words alone sent it to a web lookup.
    from core.agent_runtime.cross_chat_recall import _owned_by_another_lane, as_question

    assert not _owned_by_another_lane(as_question(text), text), as_question(text)
