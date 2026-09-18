"""A display question is not unanswerable just because the Windows reader is.

Measured live 2026-07-31 on macOS. All eight display phrasings routed correctly to
`machine.display_inspect`, and all eight were answered:

    "Display inspection is only available on Windows in this build."

On the same host, in the same runtime, `machine.inspect_specs` reported:

    - Native display resolution: 4480 x 2520
    - Current display mode: 2240 x 1260 @ 60.00Hz

`system_profiler SPDisplaysDataType` confirms both figures exactly. The reader that produced them,
`_machine_display_details`, was already in this module -- `display_inspect` simply never consulted
it and refused instead. A refusal is only honest when the answer is genuinely out of reach.
"""

from __future__ import annotations

import core.runtime_execution_tools as ret


def _unsupported(*_a, **_k):
    return {"supported": False}


def test_a_display_question_is_answered_from_the_platform_reader(monkeypatch) -> None:
    import core.machine_diagnostics as md

    monkeypatch.setattr(md, "display_info", _unsupported)
    monkeypatch.setattr(
        ret,
        "_machine_display_details",
        lambda: {
            "name": "iMac",
            "native_resolution": "4480 x 2520",
            "current_resolution": "2240 x 1260 @ 60.00Hz",
            "screen_size": "24-inch (inferred from Apple 4.5K iMac panel)",
        },
    )
    result = ret._machine_display_inspect({})
    assert result.ok is True, "the resolution was on the disk and was refused as Windows-only"
    assert result.status == "executed"
    assert "4480 x 2520" in result.response_text
    assert "2240 x 1260 @ 60.00Hz" in result.response_text
    assert "only available on Windows" not in result.response_text


def test_the_windows_reader_still_wins_when_it_has_an_answer(monkeypatch) -> None:
    """The fallback must not displace a real Windows read."""
    import core.machine_diagnostics as md

    monkeypatch.setattr(
        md,
        "display_info",
        lambda *a, **k: {
            "supported": True,
            "displays": [{"name": "Dell U2720Q", "width": 3840, "height": 2160, "refresh_hz": 60}],
            "physical": {"verified": True, "diagonal_in": 27},
        },
    )
    result = ret._machine_display_inspect({})
    assert result.ok is True
    assert "Dell U2720Q" in result.response_text
    assert "3840 x 2160" in result.response_text


def test_a_platform_with_no_reader_at_all_refuses_without_naming_windows(monkeypatch) -> None:
    """Still a refusal when nothing can read the display -- but not a false claim about why.

    The old text told a macOS or Linux user the capability was Windows-only, which is a statement
    about the build rather than about their machine, and was wrong here in any case.
    """
    import core.machine_diagnostics as md

    monkeypatch.setattr(md, "display_info", _unsupported)
    monkeypatch.setattr(ret, "_machine_display_details", dict)
    result = ret._machine_display_inspect({})
    assert result.ok is False
    assert "won't guess" in result.response_text
