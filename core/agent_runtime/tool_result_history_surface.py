from __future__ import annotations

from typing import Any

from core.agent_runtime import orchestrator as agent_orchestrator_runtime
from core.agent_runtime import response_policy as agent_response_policy


class ToolResultHistorySurfaceMixin:
    def _append_tool_result_to_source_context(
        self,
        source_context: dict[str, Any] | None,
        *,
        execution: Any,
        tool_name: str,
        receipt: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return agent_response_policy.append_tool_result_to_source_context(
            self,
            source_context,
            execution=execution,
            tool_name=tool_name,
            receipt=receipt,
        )

    def _normalize_tool_history_message(self, item: dict[str, Any]) -> dict[str, str]:
        return agent_response_policy.normalize_tool_history_message(self, item)

    def _tool_surface_for_history(self, tool_name: str) -> str:
        return agent_response_policy.tool_surface_for_history(tool_name)

    def _tool_history_observation_payload(
        self,
        *,
        execution: Any,
        tool_name: str,
        receipt: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        return agent_response_policy.tool_history_observation_payload(
            execution=execution,
            tool_name=tool_name,
            receipt=receipt,
        )

    def _tool_history_observation_prompt(self, observation: dict[str, Any]) -> str:
        return agent_orchestrator_runtime.tool_history_observation_prompt(observation)

    def _tool_history_observation_message(
        self,
        *,
        execution: Any,
        tool_name: str,
        receipt: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        return agent_response_policy.tool_history_observation_message(
            self,
            execution=execution,
            tool_name=tool_name,
            receipt=receipt,
        )


class DeferredToolBatchSurfaceMixin:
    """The calls a provider returned that this step did not run, and how the model learns of them.

    A provider may answer one turn with several schema-valid tool calls. The step loop executes
    exactly one per step, on purpose - every guard it has (repeat detection, the step budget, the
    per-call permission gate, mutating-tool receipt idempotency) is per-step, and draining a queue
    through the loop's pending slot would bypass all of them while also executing intent the model
    formed before it saw the first result.

    So the extra members are recorded rather than run, and the model is TOLD. Before this they were
    dropped silently, which is the part that actually harms: a model cannot tell "my second call
    returned nothing" from "my second call never happened", and either way its next step is a guess.
    """

    @staticmethod
    def _unexecuted_batch_calls(
        tool_decision: Any, executed_payload: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """The same members as `_unexecuted_batch_members`, carrying their ARGUMENTS.

        Names alone cannot be executed, and they cannot be reported usefully either: five deferred
        `workspace.read_file` calls render as the same word five times, with the five paths - the
        only part the operator or the model could act on - discarded.
        """

        calls = tuple(getattr(tool_decision, "tool_calls", ()) or ())
        if len(calls) < 2:
            return []
        executed_intent = str((executed_payload or {}).get("intent") or "").strip()
        members: list[dict[str, Any]] = []
        skipped_executed = False
        for call in calls:
            intent = str(getattr(call, "intent", "") or "").strip()
            if not intent:
                continue
            if not skipped_executed and intent == executed_intent:
                skipped_executed = True
                continue
            arguments = getattr(call, "arguments", None)
            members.append(
                {
                    "intent": intent,
                    "arguments": dict(arguments) if isinstance(arguments, dict) else {},
                    "_native_tool_call_id": str(getattr(call, "call_id", "") or ""),
                }
            )
        return members

    @staticmethod
    def _unexecuted_batch_members(tool_decision: Any, executed_payload: dict[str, Any]) -> list[str]:
        """Intent names the provider asked for and this step did not run, in provider order."""

        calls = tuple(getattr(tool_decision, "tool_calls", ()) or ())
        if len(calls) < 2:
            return []
        executed_intent = str((executed_payload or {}).get("intent") or "").strip()
        names: list[str] = []
        skipped_executed = False
        for call in calls:
            intent = str(getattr(call, "intent", "") or "").strip()
            if not intent:
                continue
            # `structured_output` is call #1 re-parsed from `output_text`, so the first occurrence
            # of that intent is the one being executed now. Only the FIRST is skipped: a provider
            # that genuinely asked for the same tool twice still has its second request reported.
            if not skipped_executed and intent == executed_intent:
                skipped_executed = True
                continue
            names.append(intent)
        return names

    @staticmethod
    def _note_deferred_batch_for_model(
        source_context: dict[str, Any] | None,
        *,
        deferred: list[str],
        requested: int = 0,
        ran: int = 0,
        described: list[str] | None = None,
    ) -> dict[str, Any]:
        """Put the deferred calls where the next step's model prompt will carry them.

        `requested`/`ran`/`described` come from the caller, which is the only place that knows them.
        This note used to re-derive the count as `len(deferred) + 1` and assert, in a literal, that
        "Only the first ran" - a sentence written before read-only members ran inline. Measured
        2026-08-03 on one 12-read batch: the runtime EVENT said "requested 12 ... Ran 8" while the
        model was told "you requested 5 tools ... Only the first ran", with every path discarded.
        Both numbers false, in the same round, on the two sides of the same event.

        The defaults reproduce the old arithmetic exactly, so a caller that knows neither number
        still gets a self-consistent sentence.
        """

        context = dict(source_context or {})
        if not deferred:
            return context
        names = list(described or []) or list(deferred)
        listed = ", ".join(f"`{name}`" for name in names)
        total = int(requested) if requested else len(deferred) + 1
        executed = int(ran) if ran else 1
        note = (
            f"Note: you requested {total} tools in one reply. {executed} ran, and the "
            f"{'result is' if executed == 1 else 'results are'} above. "
            f"These were NOT run: {listed}. Request any you still need now - "
            f"the {'result' if executed == 1 else 'results'} above may already answer them."
        )
        history = list(context.get("conversation_history") or [])
        history.append({"role": "user", "content": note})
        context["conversation_history"] = history
        context["deferred_tool_calls"] = list(deferred)
        return context
