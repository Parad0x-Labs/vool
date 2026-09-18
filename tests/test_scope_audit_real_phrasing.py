"""FORGE correction — the real operator phrasing that bypassed the round-2 matchers.

The round-2 tests were false-green: they used curated phrases ("audit the codebase") while the live
session used natural ones ("this work folder", "run audit on code") that slipped through. These
assertions ARE the live transcript, at the matcher level, so a regression can't go green while the
real workflow is red.
"""
from __future__ import annotations

from core.agent_runtime.workspace_audit import looks_like_code_audit_request
from core.execution.constants import (
    machine_folder_audit_intent,
    machine_folder_search_intent,
    refers_to_current_scope,
)

# --- (A) "work / working / workspace / current" folder phrases mean the BOUND scope --------------

CURRENT_SCOPE_PHRASES = (
    "audit this work folder",
    "audit this workspace folder",
    "check this work folder",
    "analyse the working folder",
    "the work folder",
    "working folder",
    "workspace folder",
    "review this workspace",
)


def test_work_and_workspace_folders_are_current_scope():
    for phrase in CURRENT_SCOPE_PHRASES:
        assert refers_to_current_scope(phrase) is True, phrase


def test_work_folder_phrases_do_not_become_a_named_search():
    # Must NOT resolve a folder literally named "work" (which substring-matched w2l_work live).
    for phrase in CURRENT_SCOPE_PHRASES:
        assert machine_folder_audit_intent(phrase) is None, phrase
        assert machine_folder_search_intent(phrase) is None, phrase


# --- (C) audit-intent detection covers the natural audit phrasing --------------------------------

AUDIT_PHRASES = (
    "run audit on code",
    "audit on code",
    "audit this workspace folder",
    "audit this work folder",
    "check code for obvious failures",
    "audit the codebase",          # the curated phrase must still work
    "do a full audit of this repo",
)


def test_audit_detection_covers_real_phrasing():
    for phrase in AUDIT_PHRASES:
        assert looks_like_code_audit_request(phrase) is True, phrase


def test_audit_detection_still_rejects_non_audits():
    for phrase in ("what's in this folder", "find my dropbox folder", "how are you", "set my name to LOOP"):
        assert looks_like_code_audit_request(phrase) is False, phrase
