"""Bounded private-record tools: require an owner turn and its proven visible words."""
from __future__ import annotations

import json

from core.my_things.prepare import prepare
from core.my_things.reading import match_text
from core.my_things.store import ThingsError, ThingsStore


def contracts():
    # Offered to the model on the owner's own turns only: the demand seat (core.tool_demand_signals) and the turn's
    # judge (core.plugin_ask) seat them, and run() below refuses anything but an owner-authored local turn whose own
    # words carry the query. A search never lists the store; it returns at most five matching summaries.
    from core.runtime_tool_contracts import RuntimeToolContract

    specs = [
        ("search", "Find only saved household things matching words the owner used in this turn. Never lists all private records.", {"query": "string (at least four characters, from the owner's message)"}),
        ("match", "Match label text the owner supplied against saved model and serial identifiers, showing exact evidence. Ambiguous results require a choice.", {"text": "string (label text from the owner's message)"}),
        ("prepare", "Prepare a draft return or repair request, warranty check or specification comparison for one saved thing named by the owner. Never sends anything.", {"id": "string (matched thing id)", "kind": "string (return|repair|warranty|fits|delivery)", "issue": "string optional", "observed": "string optional"}),
    ]
    return [RuntimeToolContract(intent="things." + name, description=description, tool_surface="my_things", capability_id="my_things.read", capability_claim="read only matched private household records and prepare drafts", supported=True, unsupported_reason="", input_schema=schema, output_schema={"result": "object"}, side_effect_class="read_only", approval_requirement="none", timeout_policy="local_only_default", retry_policy="none", artifact_emission="none", error_contract="returns_structured_error_result", permission_actions=("read_files",), model_visible=True) for name, description, schema in specs]


def run(intent, arguments, source_context):
    from core.contacts.tools import proven_user_text
    from core.request_trust import request_is_owner_local, turn_is_owner_authored
    from core.runtime_execution_tools import RuntimeExecutionResult

    def result(ok, status, message, payload=None):
        return RuntimeExecutionResult(handled=True, ok=ok, status=status, response_text=message, details={"observation": {"intent": intent, "ok": ok, "status": status, "tool_surface": "my_things", "result": payload or {}}})

    if not request_is_owner_local(source_context) or not turn_is_owner_authored(source_context):
        return result(False, "owner_required", "Open My things in your local app to use your private records.")
    words = proven_user_text(source_context)
    if not words:
        return result(False, "request_required", "Ask for one specific thing in your own message.")
    try:
        store = ThingsStore()
        # The forget law: a record carrying words this chat (or every chat) forgot is never read back to the model
        # here, the way memory and run history never bring them back. The record stays the person's, on their own
        # My things page. A ledger that cannot be read counts as "may carry forgotten words".
        from core.memory.run_history import forgotten_tokens

        tokens = forgotten_tokens(str(source_context.get("runtime_session_id") or source_context.get("session_id") or ""))
        if tokens is None:
            raise ThingsError("forget_unchecked", "Open My things to see your saved things.")
        forgotten = {record["id"] for token in tokens for record in store.matching(token)}
        records = [record for record in store.list() if record["id"] not in forgotten]
        if intent == "things.search":
            if set(arguments) - {"query"}:
                raise ThingsError("bad_fields", "Use a specific name or model number.")
            query = arguments.get("query")
            if not isinstance(query, str) or not 4 <= len(query.strip()) <= 250 or query.strip().casefold() not in words.casefold():
                raise ThingsError("query_required", "Use a specific name or model number from your message.")
            # Only matching summaries reach the observation. Files, notes and unrelated
            # entries never leave the local store through a search.
            matches = [{k: item[k] for k in ("id", "name", "maker", "model")} for item in records if any(query.strip().casefold() in item.get(k, "").casefold() for k in ("name", "maker", "model", "serial"))]
            payload = {"things": matches[:5], "more_matches": len(matches) > 5}
        elif intent == "things.match":
            if set(arguments) - {"text"}:
                raise ThingsError("bad_fields", "Use the label text from your message.")
            text = arguments.get("text")
            if not isinstance(text, str) or text.casefold() not in words.casefold():
                raise ThingsError("text_required", "Use the label text from your own message.")
            payload = match_text(text, records)
        elif intent == "things.prepare":
            if set(arguments) - {"id", "kind", "issue", "observed"}:
                raise ThingsError("bad_fields", "Choose one saved thing and a draft action.")
            if arguments.get("id") in forgotten:
                raise ThingsError("thing_required", "Open My things to use that saved thing.")
            record = store.get(arguments.get("id"))
            if record["name"].casefold() not in words.casefold():
                raise ThingsError("thing_required", "Name the saved thing in your own message.")
            for field in ("issue", "observed"):
                value = arguments.get(field, "")
                if not isinstance(value, str) or (value and value.casefold() not in words.casefold()):
                    raise ThingsError("text_required", "Use the details from your own message.")
            payload = prepare(record, kind=arguments.get("kind"), issue=arguments.get("issue", ""), observed=arguments.get("observed", ""), arrived=[])
        else:
            return result(False, "unknown_action", "That action is not available for My things.")
        return result(True, "ok", json.dumps(payload, ensure_ascii=False), payload)
    except ThingsError as exc:
        return result(False, exc.code, exc.message)
    except (OSError, ValueError, RuntimeError):
        return result(False, "store_unavailable", "Your private records could not be opened safely.")
