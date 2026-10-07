"""Real served workflow for the paired300 authority repair (owner-review D4).

Proof level: every turn here is a REAL ``VoolAgent.run_once`` call in a fresh
interpreter process over a disposable runtime home — normal ``store_turn``
ingestion, real routing / retrieval / context assembly / prompt normalizer /
invocation sealing / post-generation guards, nothing mocked on that path. The
ONE scripted element is the external transport boundary: the model endpoint
(``requests.post``) and the provider health probe (``requests.get``) are
replaced by a recording sink that answers each call with a per-turn SCRIPTED
CHOICE. Every replaced call is recorded (method, URL, payload) and asserted to
stay on the dead scripted-provider address, so no paid or remote service can be
reached. The scripted replies are transport-boundary choices, NOT live-model
accuracy; what this file proves is the deterministic served chain: what the
store admits, what the serialized request carried, and what the guards deliver.

Covers, in one ordinary allotment world (no single giant question): a supported
past duration, a personal record update, assistant later-entry + ordinal
attribution, a retained preference used for advice, refusal with missing /
negated / foreign evidence, deletion/requery with the complete unaffected
sibling fact, and capsule flag-off. Captured per turn: question, selected
evidence, scripted raw reply, final delivered reply, model-call count, and the
actual serialized request payload; plus the embedding backend receipt, the
deletion outcome, and the source head.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid

_DRIVER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_p300_served_authority_driver.py")
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MAIN_CHAT = "allotment-log"
FOREIGN_CHAT = "neighbor-notes"
PROVIDER_MODEL = "p300-served-scripted"
PROVIDER_BASE = "http://127.0.0.1:9"

SEED = [
    {
        # The distiller's importance gate retains a user statement that carries
        # a digit+unit duration ("for 7 months"). The first phrasing of this
        # world spelled the number out ("about seven months") and the
        # contracted "I've" matched no acquisition/possessive/digit signal, so
        # the statement scored 0.2 and was never distilled — recorded here as
        # the finding that motivated the phrasing.
        "chat": MAIN_CHAT,
        "user": (
            "I have been running my drip irrigation line for 7 months now, "
            "since early March."
        ),
        "assistant": (
            "Noted — the drip irrigation line has been running for 7 months, "
            "since early March."
        ),
        "stated": "2026-09-25",
    },
    {
        "chat": MAIN_CHAT,
        "user": "My heaviest marrow this year weighed 14 kilograms.",
        "assistant": "A 14-kilogram marrow is a strong one.",
        "stated": "2026-09-12",
    },
    {
        "chat": MAIN_CHAT,
        "user": "Update: my heaviest marrow is now 17 kilograms.",
        "assistant": "Noted — 17 kilograms is your heaviest marrow now.",
        "stated": "2026-09-27",
    },
    {
        # A retained preference needs a possessive ("my spare fence
        # insulators") for the importance gate to keep it — the possessive-free
        # first phrasing scored 0.2 and was dropped (finding recorded in the
        # mission report).
        "chat": MAIN_CHAT,
        "user": (
            "Before every winter check I always pack my spare fence insulators "
            "and the spare battery into the red toolbox."
        ),
        "assistant": (
            "Got it — the fence insulators and the spare battery live in the "
            "red toolbox for winter checks."
        ),
        "stated": "2026-09-18",
    },
    {
        "chat": MAIN_CHAT,
        "user": "My longest bean row is not 12 metres anymore, it is 9 metres.",
        "assistant": "Updated — the longest bean row is 9 metres.",
        "stated": "2026-09-22",
    },
    {
        "chat": MAIN_CHAT,
        "user": "The water butt by the polytunnel holds 210 litres.",
        "assistant": "Noted — the water butt holds 210 litres.",
        "stated": "2026-09-15",
    },
    {
        # The ranked list is the ASSISTANT's later entry, so the ordinal ask
        # must be answered from "- assistant said" occurrence evidence — the
        # attribution law under proof. The user side is only the request.
        "chat": MAIN_CHAT,
        "user": (
            "Can you rank the plot shed for me, best first? The broad bean "
            "supports can come down next week, and twelve bamboo canes are "
            "enough for the fall sowing."
        ),
        "assistant": (
            "Storage ranking, best first:\n"
            "1. The west bay rack holds the onion strings.\n"
            "2. The cellar step shelf keeps the squash crates.\n"
            "3. The brassica cage shelf stores the seed trays.\n"
            "4. The cold frame lid stack is last."
        ),
        "stated": "2026-09-21",
    },
    {
        "chat": FOREIGN_CHAT,
        "user": "My tallest sunflower this year is 2.4 metres.",
        "assistant": "An impressive sunflower.",
        "stated": "2026-09-26",
    },
]

# (id, question, scripted raw reply — a transport-boundary choice, labelled)
TURNS_MAIN = [
    (
        "past-duration",
        "How long have I been running the drip irrigation line?",
        "You've been running the drip irrigation line for about 7 months, "
        "since early March.",
    ),
    (
        "record-update",
        "What's my heaviest marrow?",
        "Your current heaviest marrow is 17 kilograms, which beats the one from "
        "earlier that autumn.",
    ),
    (
        # "What's ..." routes through the exact-recall branch, and the phrasing
        # deliberately avoids this-machine vocabulary ("storage", "slot",
        # "what's on ...") that the first phrasing hit: "What's on the third
        # shelf slot in my storage ranking?" was claimed by the local-fact
        # tool gate ('your drives') and refused before any recall.
        "assistant-ordinal",
        "What's the third shelf in the plot shed ranking you gave me?",
        "The third shelf in your plot shed ranking is the brassica cage shelf "
        "— it stores the seed trays.",
    ),
    (
        # A generic advice envelope ("what should I do") so the advice-topic
        # probe can find the retained preference; "winter check" is the
        # preference's own topic vocabulary, naturally.
        "retained-preference-advice",
        "The first hard freeze is coming — what should I do about my winter "
        "check?",
        "Before the first hard freeze, do your usual winter check: pack the "
        "spare fence insulators and the spare battery — they live in the red "
        "toolbox.",
    ),
    (
        "refuse-missing-and-foreign",
        "How tall is my tallest sunflower this year?",
        "Your tallest sunflower this year is 2.4 metres.",
    ),
    (
        # "current" makes the scripted wrong value a current-anchored record
        # claim, which is the shape the negation law judges: a value the
        # same-subject line negates is refused.
        "refuse-negated",
        "How long is my longest bean row?",
        "Your current longest bean row is 12 metres.",
    ),
]

TURNS_AFTER_DELETE = [
    (
        "deleted-fact-requery",
        "What's my heaviest marrow?",
        "Your current heaviest marrow is 17 kilograms.",
    ),
    (
        # Recall-style ask: "How much does my water butt hold?" routed to the
        # live-web amount lane, never reaching the store.
        "unaffected-sibling",
        "What did I tell you about the water butt?",
        "Your water butt holds 210 litres.",
    ),
]

TURNS_FLAG_OFF = [
    (
        "flag-off-no-capsule",
        "How long have I been running the drip irrigation line?",
        "You've been running the drip irrigation line for about 7 months, as "
        "you said before.",
    ),
]


def _run_driver(tmp_path, *, name, seed, skip_seed, flag_off, delete_token):
    turns_payload = {"turns": []}
    home = tmp_path / f"served-{name}-{uuid.uuid4().hex[:8]}"
    home.mkdir(parents=True)
    turns_file = tmp_path / f"turns-{name}.json"
    out_file = tmp_path / f"served-{name}-result.json"
    import argparse

    turns_doc = {
        "past-duration": TURNS_MAIN[0],
        "record-update": TURNS_MAIN[1],
        "assistant-ordinal": TURNS_MAIN[2],
        "retained-preference-advice": TURNS_MAIN[3],
        "refuse-missing-and-foreign": TURNS_MAIN[4],
        "refuse-negated": TURNS_MAIN[5],
        "deleted-fact-requery": TURNS_AFTER_DELETE[0],
        "unaffected-sibling": TURNS_AFTER_DELETE[1],
        "flag-off-no-capsule": TURNS_FLAG_OFF[0],
    }
    wanted = (
        [t[0] for t in TURNS_MAIN]
        if name == "main"
        else ([t[0] for t in TURNS_AFTER_DELETE] if name == "after-delete" else [t[0] for t in TURNS_FLAG_OFF])
    )
    turns_payload = {
        "turns": [
            {"id": tid, "question": turns_doc[tid][1], "scripted": turns_doc[tid][2]}
            for tid in wanted
        ]
    }
    turns_file.write_text(json.dumps(turns_payload))
    seed_file = None
    if seed is not None:
        seed_file = tmp_path / f"seed-{name}.json"
        seed_file.write_text(json.dumps(seed))
    cmd = [
        sys.executable,
        _DRIVER,
        "--home",
        str(home),
        "--out",
        str(out_file),
        "--repo-root",
        _REPO,
        "--turns-json",
        str(turns_file),
    ]
    if seed_file:
        cmd += ["--seed-json", str(seed_file)]
    if skip_seed:
        cmd += ["--skip-seed"]
    if flag_off:
        cmd += ["--flag-off"]
    if delete_token:
        cmd += ["--delete-token", delete_token]
    result = subprocess.run(
        cmd, cwd=_REPO, capture_output=True, text=True, timeout=600
    )
    assert result.returncode == 0, (
        f"driver {name} failed:\n{result.stdout[-3000:]}\n{result.stderr[-3000:]}"
    )
    return json.loads(out_file.read_text())


def _turn(document, turn_id):
    matches = [t for t in document["turns"] if t["id"] == turn_id]
    assert len(matches) == 1, (turn_id, [t["id"] for t in document["turns"]])
    return matches[0]


def _all_calls_on_scripted_provider(document):
    """Every REPLACED transport call stays on the dead scripted address and is
    disclosed with its URL. The local embedding backend (127.0.0.1:11434) is an
    allowed local service: its calls are recorded as passthrough, never scripted."""
    for turn in document["turns"]:
        for call in turn["request_calls"]:
            if call.get("local_passthrough"):
                assert str(call["url"]).startswith("http://127.0.0.1:11434"), (turn["id"], call)
                continue
            assert str(call["url"]).startswith(PROVIDER_BASE), (turn["id"], call)
            assert call["method"] in {"POST", "GET"}, (turn["id"], call)


def _payload_bytes(turn):
    return "\n".join(
        call.get("payload_bytes") or "" for call in turn["request_calls"]
    )


def test_served_workflow_main_branches(tmp_path):
    document = _run_driver(
        tmp_path, name="main", seed=SEED, skip_seed=False, flag_off=False, delete_token=""
    )
    assert document["proof_level"].startswith("REAL VoolAgent.run_once")
    assert document["source_head"], document
    assert document["embedding_backend"], document
    # normal ingestion really ran: every seeded turn either distilled into the
    # semantic index ("stored") or retained its source occurrences ("retained")
    assert len(document["seed_receipts"]) == len(SEED), document["seed_receipts"]
    assert all(
        receipt["status"] in {"stored", "retained"}
        for receipt in document["seed_receipts"]
    ), document["seed_receipts"]
    _all_calls_on_scripted_provider(document)

    # supported past duration: the raw answer survives the past-time guard
    # because the guard reads the same admitted capsule the request carried —
    # the fact line is inside the captured request payload AND inside the
    # guard-visible admitted evidence
    duration = _turn(document, "past-duration")
    assert "7 months" in duration["delivered"], duration
    assert "7 months" in _payload_bytes(duration), _payload_bytes(duration)[:600]
    assert "7 months" in duration["admitted_capsule"], duration["admitted_capsule"][:400]

    # personal record update: the improved record ships; the request carried
    # the dated record lines
    record = _turn(document, "record-update")
    assert "17 kilograms" in record["delivered"], record
    assert "17 kilograms" in _payload_bytes(record)

    # assistant later-entry + ordinal attribution: the third ranking slot is
    # bound from the assistant's later entry, attributed assistant-said — the
    # request carried that line, never a user-said form of it
    ordinal = _turn(document, "assistant-ordinal")
    assert "brassica cage shelf" in ordinal["delivered"], ordinal
    assert "seed trays" in ordinal["delivered"], ordinal
    assert "seed trays" in _payload_bytes(ordinal), _payload_bytes(ordinal)[:600]
    assistant_lines = [
        line
        for line in ordinal["selected_facts"]
        if line.startswith("- assistant said")
    ]
    assert any("seed trays" in line for line in assistant_lines), ordinal["selected_facts"]
    assert not any(
        line.startswith("- user said") and "seed trays" in line
        for line in ordinal["selected_facts"]
    ), ordinal["selected_facts"]

    # retained preference used in advice: the serialized request itself
    # carried the user-owned preference the advice turns on
    advice = _turn(document, "retained-preference-advice")
    assert "insulators" in _payload_bytes(advice), _payload_bytes(advice)[:600]
    assert "insulators" in advice["delivered"], advice

    # refusal with missing evidence AND foreign-scope exclusion: the value
    # exists only in another chat, reaches neither the request nor delivery
    missing = _turn(document, "refuse-missing-and-foreign")
    assert "2.4 metres" not in missing["delivered"], missing
    assert "2.4 metres" not in _payload_bytes(missing)
    assert "not going to state" in missing["delivered"], missing

    # refusal with negated evidence: the retracted value never ships
    negated = _turn(document, "refuse-negated")
    assert "12 metres" not in negated["delivered"], negated
    assert "not going to state" in negated["delivered"], negated


def test_served_workflow_deletion_requery_and_sibling(tmp_path):
    document = _run_driver(
        tmp_path,
        name="after-delete",
        seed=SEED,
        skip_seed=False,
        flag_off=False,
        delete_token="marrow",
    )
    assert document["deletion"]["nodes"] >= 1, document["deletion"]
    assert document["deletion"]["occurrences"] >= 1, document["deletion"]
    _all_calls_on_scripted_provider(document)

    # the deleted fact's VALUES are gone from the request and refused at
    # delivery (the question itself may still name the subject — "marrow" —
    # but no record value may ride the request)
    deleted = _turn(document, "deleted-fact-requery")
    assert "17 kilograms" not in _payload_bytes(deleted), _payload_bytes(deleted)[:600]
    assert "14 kilograms" not in _payload_bytes(deleted), _payload_bytes(deleted)[:600]
    assert "17 kilograms" not in deleted["delivered"], deleted
    assert "not going to state" in deleted["delivered"], deleted

    # the complete unaffected sibling fact still serves
    sibling = _turn(document, "unaffected-sibling")
    assert "210 litres" in _payload_bytes(sibling), _payload_bytes(sibling)[:600]
    assert "210 litres" in sibling["delivered"], sibling


def test_served_workflow_flag_off_degrades_safely(tmp_path):
    document = _run_driver(
        tmp_path, name="flag-off", seed=SEED, skip_seed=False, flag_off=True, delete_token=""
    )
    _all_calls_on_scripted_provider(document)
    flagged = _turn(document, "flag-off-no-capsule")
    # no capsule was injected, no admitted evidence exists, and the specific
    # duration is honestly refused rather than echoed
    assert flagged["admitted_capsule"] == "", flagged["admitted_capsule"][:200]
    assert flagged["admitted_capsule_source"] == "none", flagged
    assert "7 months" not in flagged["delivered"], flagged
    assert "not going to state" in flagged["delivered"], flagged
