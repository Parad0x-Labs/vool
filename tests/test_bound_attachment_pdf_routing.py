"""A bound PDF attachment owns its turn even when the message types its filename.

The sibling of the display arbitration: the disk-PDF fast path searches THIS machine's
folders for the named file, and a turn that carries the named PDF as a bound attachment
must never be answered with "I could not find a file called ... anywhere in ..." when the
extraction the user asked about is already staged on the turn. ``on screen`` phrasing is
the reported collision shape: it names the attached document AND reads like a host-display
question, and both misreadings lose the attachment.
"""


from core.agent_runtime import fast_paths_pdf

PDF_QUESTION = "What is on screen on page 2 of the attached scan-vk73.pdf?"
BOUND_PDF_CONTEXT = {
    "runtime_session_id": "pdf-arbitration",
    "attachment_turn_id": "pdf-arbitration-turn",
    "external_evidence": [{
        "origin": "chat_attachment",
        "attachment_id": "attachment-issued-by-ingress",
        "name": "scan-vk73.pdf",
        "kind": "artifact",
        "reader_format": "pdf",
    }],
}


class _RecordingAgent:
    def __init__(self) -> None:
        self.claims: list[dict] = []

    def _fast_path_result(self, *, user_input, response, confidence, source_context, reason, **_kw):
        self.claims.append({"response": response, "reason": reason})
        return {"response": response, "fast_path": reason}


def test_on_screen_pdf_phrasing_naming_a_bound_attachment_is_not_hijacked_by_the_disk_lane():
    agent = _RecordingAgent()
    result = fast_paths_pdf.maybe_handle_pdf_read(
        agent, PDF_QUESTION, session_id="pdf-arbitration", source_context=BOUND_PDF_CONTEXT,
    )
    assert result is None, "the disk-PDF lane answered a turn whose PDF was bound to it"
    assert agent.claims == [], f"the turn was claimed with: {agent.claims}"


def test_attached_pdf_named_without_on_screen_phrasing_stays_with_the_attachment_pipeline():
    agent = _RecordingAgent()
    result = fast_paths_pdf.maybe_handle_pdf_read(
        agent,
        "What does the attached scan-vk73.pdf say on its last page?",
        session_id="pdf-arbitration",
        source_context=BOUND_PDF_CONTEXT,
    )
    assert result is None
    assert agent.claims == []


def test_an_attached_name_that_matches_nothing_bound_leaves_the_disk_lane_alive():
    """The guard is a NAME match, not a blanket stand-down: a real disk request still works."""
    agent = _RecordingAgent()
    unrelated = {
        "runtime_session_id": "pdf-arbitration",
        "attachment_turn_id": "pdf-arbitration-turn",
        "external_evidence": [{
            "origin": "chat_attachment",
            "attachment_id": "attachment-issued-by-ingress",
            "name": "photo-of-the-whiteboard.png",
            "kind": "image",
        }],
    }
    result = fast_paths_pdf.maybe_handle_pdf_read(
        agent,
        "Read the notes in meeting-minutes-vk73.pdf for me",
        session_id="pdf-arbitration",
        source_context=unrelated,
    )
    assert result is not None, "the disk lane stopped working the moment any attachment was bound"
    assert "meeting-minutes-vk73.pdf" in agent.claims[0]["response"]


def test_the_same_message_without_a_bound_attachment_still_reaches_the_disk_lane():
    agent = _RecordingAgent()
    result = fast_paths_pdf.maybe_handle_pdf_read(
        agent, PDF_QUESTION, session_id="pdf-arbitration", source_context=None,
    )
    assert result is not None, "a genuinely missing disk file must still say so"
    assert "scan-vk73.pdf" in agent.claims[0]["response"]
