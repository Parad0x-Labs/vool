"""Guard: every /chat composer control must have a real mechanism behind it.

The Windows audit drove the page's own HTTP calls and found three controls that shipped with no
consumer: the local model tiers (`local-fast`/`local-daily`/`local-heavy` matched no manifest and
all collapsed onto the one installed model), the Effort steps (`faster`/`balanced` routed
byte-identically; only `smarter` branched), and a "Bypass permissions" mode that no handler read
and that therefore behaved exactly like Build while wearing a security label.

These tests pin the remediated surface: the controls that survive are the ones a consumer reads,
the dead ones must not come back, and the API accepts only the modes that have a handler.
"""

from __future__ import annotations

import unittest
from typing import Any
from unittest import mock

from apps.vool_api_server import _dispatch_post
from core.vool_chat_page import render_vool_chat_html
from core.web.api.runtime import RuntimeServices

HTML = render_vool_chat_html()


# --------------------------------------------------------------------------- #
# Frontend: only controls with a backend mechanism are rendered
# --------------------------------------------------------------------------- #
def test_mode_options_are_limited_to_the_handled_modes() -> None:
    for label in ("Manual", "Review edits", "Plan", "Auto", "Bypass permissions"):
        assert ">" + label + "</span>" in HTML, label
    for value in ('data-mode="manual"', 'data-mode="review_edits"', 'data-mode="plan"', 'data-mode="auto"', 'data-mode="bypass_permissions"'):
        assert value in HTML, value
    assert 'data-mode="ask"' not in HTML
    assert 'data-mode="build"' not in HTML


def test_bypass_permissions_has_real_confirmation_expiry_and_revoke_controls() -> None:
    assert 'data-mode="bypass"' not in HTML
    for token in ('id="bypassOverlay"', 'id="bypassDuration"', 'id="bypassConfirm"', 'id="bypassRevoke"', "op: 'request_bypass_confirmation'", "confirmation_id: minted.confirmation_id", "op: 'activate_bypass'", "op: 'revoke_bypass'"):
        assert token in HTML
    assert "Automatic expiry" in HTML
    assert "Highest-risk mode" in HTML


def test_dead_local_model_tiers_are_gone() -> None:
    # These values matched no provider manifest; all three ran the same installed model as Auto.
    for value in ('data-model="local-fast"', 'data-model="local-daily"', 'data-model="local-heavy"'):
        assert value not in HTML, value
    # VOOL Auto remains -- the router really does pick the local model per turn -- and the popover
    # says so instead of offering speed tiers that cannot differ.
    assert 'data-model="vool"' in HTML
    assert "VOOL Auto" in HTML
    assert "Starts local" in HTML
    assert "Auto never selects a paid model" in HTML


def test_effort_control_is_gone() -> None:
    # faster/balanced had no consumer at all and smarter reliably killed the turn on an 8GB box.
    for value in ('data-effort="faster"', 'data-effort="balanced"', 'data-effort="smarter"'):
        assert value not in HTML, value
    assert 'id="effortPop"' not in HTML
    assert "vool_effort" not in HTML
    assert "effort: currentEffort" not in HTML


def test_the_attach_control_is_live_not_decorative() -> None:
    # The old control shipped permanently disabled ("coming in Settings") and could never be
    # clicked; that shape is gone for good. The control that replaced it is wired end to end:
    # a native picker whose accept list comes from the server's own limits, a change handler
    # that stages the files through the raw upload door, and a strip the chips render into.
    assert "coming in Settings" not in HTML
    assert 'id="attachBtn"' in HTML and "disabled" not in HTML.split('id="attachBtn"', 1)[1].split(">", 1)[0]
    assert 'id="attachInput"' in HTML and "multiple" in HTML.split('id="attachInput"', 1)[1].split(">", 1)[0]
    assert "attachInputEl.addEventListener('change'" in HTML
    assert "fetch('/api/chat/attachments/upload'" in HTML
    assert "fetch('/api/chat/attachments/limits')" in HTML
    assert 'id="attachStrip"' in HTML and 'id="attachChips"' in HTML


def test_send_body_transmits_the_surviving_selections() -> None:
    # Auto stickiness still resolves the model at send (falling back to modelValue); DISPATCHER
    # phase 1 scopes it to the sending chat and moved the body into buildTurnRequestBody().
    assert "effectiveModel(chatId, lastUser.content || '')" in HTML
    assert "    model: modelId," in HTML
    # Every selection still rides out on the turn -- now read from the OWNING chat's bucket
    # (`owner`), so a turn carries the mode/grants of the chat that started it rather than
    # whatever the composer is showing when the request is built.
    assert "const owner = ownerOf(run);" in HTML
    assert "mode: owner.mode," in HTML
    assert "approval_token: owner.approvalToken," in HTML
    assert "bypass_token: (owner.bypassGrant && owner.bypassGrant.token) || ''," in HTML
    for keep in (
        "messages: canonicalMessagesForModel(owner.history)",
        "stream: true",
        "stream_task_events: true",
        "session_id: run.chatId",
    ):
        assert keep in HTML, keep


def test_generic_pending_receipt_cannot_overwrite_an_exact_approval_request() -> None:
    assert "if (!a.approval_id) return;" in HTML


def test_selections_persist_in_localstorage() -> None:
    assert "localStorage.setItem('vool_modes_v1'" in HTML
    assert "_jget('vool_modes_v1'" in HTML
    # The mode is persisted against the chat it belongs to. DISPATCHER phase 1 binds that chat id
    # before the controller round-trip, so a chat switch mid-request cannot file one chat's mode
    # preference under another's.
    assert "saveModeForSession(chatId, applied);" in HTML
    assert "const chatId = displayedChat, owner = chatState(chatId);" in HTML
    assert "localStorage.setItem('vool_chat_models_v1'" in HTML
    assert "_jget('vool_chat_models_v1'" in HTML
    assert "choices[String(chatId)] = m || 'vool'" in HTML
    assert "localStorage.getItem('vool_model'" in HTML
    assert "localStorage.setItem('vool_allow_" not in HTML


def test_active_selection_is_marked_and_reflected_in_label() -> None:
    assert "function reflectMode" in HTML
    assert "function reflectModel" in HTML
    assert "function reflectEffort" not in HTML
    assert "initComposerControls()" in HTML
    assert "'✓'" in HTML
    assert "function setModeController" in HTML
    assert "fetch('/api/mode'" in HTML
    assert "e.key !== 'ArrowDown' && e.key !== 'ArrowUp'" in HTML
    assert "selected.focus()" in HTML
    assert "e.key === 'Enter' || e.key === ' '" in HTML


# --------------------------------------------------------------------------- #
# Backend: only modes with a handler reach source_context
# --------------------------------------------------------------------------- #
class ComposerControlsSourceContextTests(unittest.TestCase):
    def _dispatch_capture(self, body: dict[str, Any]) -> dict[str, Any]:
        runtime = RuntimeServices(display_name="VOOL")
        seen: list[dict[str, Any]] = []

        def fake_run_agent(
            user_text: str,
            *,
            session_id: str | None = None,
            source_context: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            seen.append(dict(source_context or {}))
            return {"response": "ok", "confidence": 1.0}

        with mock.patch("apps.vool_api_server._run_agent", side_effect=fake_run_agent):
            response = _dispatch_post(
                path="/api/chat",
                body=body,
                headers={"content-type": "application/json"},
                runtime=runtime,
                model_name="vool",
                workspace_root_provider=lambda: "/tmp",
            )
        self.assertEqual(response.status, 200)
        self.assertEqual(len(seen), 1)
        return seen[0]

    def test_handled_mode_is_read_into_source_context(self) -> None:
        ctx = self._dispatch_capture({
            "model": "vool",
            "mode": "Plan",       # server lower-cases it
            "messages": [{"role": "user", "content": "hello"}],
        })
        self.assertEqual(ctx["operating_mode"], "plan")

    def test_unhandled_mode_resolves_to_manual_rather_than_being_dropped(self) -> None:
        # "bypass" is not a supported value. Dropping it used to be the strict answer, but dropping
        # it left the turn with no mode -- and no mode was the permission bypass, so the value that
        # named a bypass got one by being unrecognised. It resolves to Manual now: the strictest
        # mode, never the one it asked for.
        ctx = self._dispatch_capture({
            "model": "vool",
            "mode": "bypass",
            "messages": [{"role": "user", "content": "hello"}],
        })
        self.assertEqual(ctx["operating_mode"], "manual")

    def test_unconsumed_effort_level_is_not_recorded(self) -> None:
        # faster/balanced have no consumer anywhere, so they must not be stored as if they did.
        ctx = self._dispatch_capture({
            "model": "vool",
            "effort": "Balanced",
            "messages": [{"role": "user", "content": "hello"}],
        })
        self.assertNotIn("effort", ctx)
        self.assertNotIn("autopilot_allow_heavy_model", ctx)

    def test_effort_smarter_still_sets_the_real_allow_heavy_hook(self) -> None:
        # The one effort value with real machinery behind it stays wired for API callers, even
        # though the UI no longer offers it (it collapses the ranking on a single-manifest box).
        ctx = self._dispatch_capture({
            "model": "vool",
            "effort": "smarter",
            "messages": [{"role": "user", "content": "hello"}],
        })
        self.assertNotIn("effort", ctx)
        self.assertIs(ctx["autopilot_allow_heavy_model"], True)

    def test_absent_controls_leave_source_context_untouched_except_the_mode(self) -> None:
        """An unsent control stays unset -- but the operating mode is not one of those controls.

        It used to be: an absent mode left `operating_mode` off the context, and downstream an
        absent `operating_mode` meant the permission decision was skipped altogether. The mode is
        now stamped on every turn by the controller, defaulting to Manual, so "the client sent no
        mode" costs an approval prompt instead of granting silent write access.
        """
        ctx = self._dispatch_capture({
            "model": "vool",
            "messages": [{"role": "user", "content": "hello"}],
        })
        self.assertEqual(ctx["operating_mode"], "manual")
        self.assertNotIn("effort", ctx)
        self.assertNotIn("autopilot_allow_heavy_model", ctx)


if __name__ == "__main__":
    unittest.main()
