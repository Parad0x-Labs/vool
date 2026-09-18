"""A hardware word, or the word "process", is not a request to inspect this machine.

Instances nine and ten of the defect this repo keeps finding. Driven at the live daemon on
ebafe49:

  specs lane, ~2s each, answered "Machine specs for this host:"
    - "Our new hire asked what CPU means, can you give her a one paragraph answer?"
    - "My son wants a gaming laptop and keeps talking about how much RAM it needs, ...?"
    - "What is the difference between a CPU and a GPU in plain language for a blog post?"
    - "Explain how an operating system decides which process gets CPU time next."

  process lane, ~0.2s each, answered "Top processes by memory:"
    - "Our deployment process uses a lot of memory on the CI box, not on my laptop."
    - "Write a blog post about which apps are running in the background on modern phones."
    - "My manager asked what programs are open to graduates without a technical degree."

Both detectors already excluded the other TOOL LANES a hardware word can belong to. Neither
covered the commoner case: the sentence is not addressed to this runtime at all.
"""
from __future__ import annotations

import pytest

from core.agent_runtime.fast_paths_machine import looks_like_machine_specs_question
from core.execution.constants import (
    asks_about_running_processes,
    machine_live_load_intent,
)

# Verbatim from the 2026-07-30 live run, plus the same-shape sentences that were screened with it.
PROSE_WITH_A_HARDWARE_WORD = (
    "Our new hire asked what CPU means, can you give her a one paragraph answer?",
    "The job spec says five years of experience but the salary band says junior, which is odd.",
    "I need to write the technical specs for the new sensor housing before Friday.",
    "My son wants a gaming laptop and keeps talking about how much RAM it needs, what should I tell him?",
    "The memory of that trip to Nida is the only thing keeping me going this winter.",
    'Please proofread this line from the datasheet: "The chip runs at 3.2 GHz under sustained load."',
    "In the meeting notes, mark that the hardware team still owes us the GPU benchmark numbers.",
    "What is the difference between a CPU and a GPU in plain language for a blog post?",
    "The specification document for the API is out of date and nobody wants to own it.",
    "My colleague says his machine has 64 GB of RAM and still swaps, is that plausible?",
    "Can you summarise this paragraph about processor architecture from the textbook?",
    "The band's new album is called Hardware and it is surprisingly good.",
    "Explain how an operating system decides which process gets CPU time next.",
)

# Retest set: written after the fix landed, never used to shape it.
MORE_PROSE_WITH_A_HARDWARE_WORD = (
    "The invoice lists two GPUs we never received, so accounts payable is holding it.",
    "Her thesis is about how memory works in people with early dementia.",
    "Draft a job ad for a systems engineer who knows CPU scheduling inside out.",
    "The core of the argument is that specs should be written before code, not after.",
    "My nephew broke his laptop screen and wants to know if it is worth repairing.",
    "Translate this into Lithuanian: the device has eight cores and sixteen gigabytes of memory.",
    "In the retrospective, the team said the display of results was confusing.",
    "The chip shortage of 2021 pushed second-hand machine prices up by half.",
)

PROSE_WITH_THE_WORD_PROCESS = (
    "The onboarding process is running late because HR has not signed off yet.",
    "Our deployment process uses a lot of memory on the CI box, not on my laptop.",
    "Write a blog post about which apps are running in the background on modern phones.",
    "The peace process in the region has been active since the 1990s.",
    "In court, due process means the state cannot just take your property.",
    "My manager asked what programs are open to graduates without a technical degree.",
    "The bakery's proofing process takes six hours and cannot be rushed.",
    "Which running shoes are best for someone with flat feet and a heavy heel strike?",
    "The application process for the grant is open until the end of September.",
    "Our hiring process uses too much of everyone's time and nobody owns it.",
    "The refund process takes ten working days according to their terms.",
    "Document the release process so a new starter can run it without help.",
)

REAL_SPEC_QUESTIONS = (
    "what are my machine specs",
    "machine specs",
    "how much ram do i have",
    "what cpu is in this machine?",
    "my machine specs",
    "what gpu do i have",
    "how many cores does this laptop have",
    "what chip is this mac running",
    "system specs",
    "show me the hardware specs",
    "what is my screen resolution",
    "how much vram does this gpu have",
    "give me the pc specs",
)

REAL_PROCESS_QUESTIONS = (
    "which apps are using the most memory",
    "list the top processes by cpu usage",
    "what is eating my cpu",
    "show me running processes",
    "top processes",
    "whats using all my ram",
    "which programs are open",
    "what processes are running",
    "cpu usage",
    "what applications are active right now",
    "memory hogs",
)


@pytest.mark.parametrize("prose", PROSE_WITH_A_HARDWARE_WORD + MORE_PROSE_WITH_A_HARDWARE_WORD)
def test_prose_with_a_hardware_word_is_not_a_spec_question(prose: str) -> None:
    assert looks_like_machine_specs_question(prose) is False, prose


@pytest.mark.parametrize("prose", PROSE_WITH_THE_WORD_PROCESS)
def test_prose_with_the_word_process_does_not_rank_this_machine(prose: str) -> None:
    assert machine_live_load_intent(prose) is None, prose
    assert asks_about_running_processes(prose) is False, prose


@pytest.mark.parametrize("question", REAL_SPEC_QUESTIONS)
def test_a_real_spec_question_still_reaches_the_spec_lane(question: str) -> None:
    assert looks_like_machine_specs_question(question) is True, question


@pytest.mark.parametrize("question", REAL_PROCESS_QUESTIONS)
def test_a_real_process_question_still_reaches_the_process_lane(question: str) -> None:
    assert asks_about_running_processes(question) is True, question
