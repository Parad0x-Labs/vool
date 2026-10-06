"""Explicit hydrated-history adapter for offline native guard controls.

Reads only an exited authorized fixture writer's active same-chat user rows.
Hydrated history never becomes a current-observation or user-material receipt.
The established driver still records the actual provider boundary and guards.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import runpy
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--home", required=True)
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--out", required=True)
    args, _ = parser.parse_known_args()
    os.environ["VOOL_HOME"] = os.path.abspath(args.home)
    os.environ["VOOL_HOME"] = os.path.abspath(args.home)
    sys.path.insert(0, args.repo_root)
    from core.runtime_paths import configure_runtime_home

    configure_runtime_home(args.home)
    from core.web.api import runtime

    writer = json.loads(Path(os.environ["VOOL_CURRENT_INPUT_HYDRATION_FROM"]).read_text())
    rows = [row for row in writer["retained_sources"]
            if row["chat_scope"] == "allotment-log" and row["status"] == "active"
            and row["role"] == "user" and row["authority"] == "observed-user-statement"]
    occurrence = os.environ["VOOL_CURRENT_INPUT_HYDRATION_OCCURRENCE"]
    rows = [row for row in rows if row["occurrence_id"] == occurrence]
    assert len(rows) == 1, "Declared historical fixture exchange is not active and authorized"
    # An explicit previous exchange preserves the production adjacency contract.
    # This selector is fixture ingress, not a claim of automatic source discovery.
    history = [{"role": "user", "content": rows[0]["body"]},
               {"role": "assistant", "content": "Noted as a historical user statement."}]
    original = runtime.run_agent

    def with_history(*arguments, **kwargs):
        context = dict(kwargs.get("source_context") or {})
        assert context.get("chat_id") == "allotment-log"
        # This is explicit historical ingress material, never evidence that a
        # volatile value was observed on the current turn.
        context["conversation_history"] = history + list(context.get("conversation_history") or [])
        context["client_conversation_history"] = history + list(context.get("client_conversation_history") or [])
        kwargs["source_context"] = context
        return original(*arguments, **kwargs)

    runtime.run_agent = with_history
    driver = Path(__file__).with_name("_memory_evidence_contract_driver.py")
    module = runpy.run_path(str(driver))
    assert module["main"]() == 0
    out = Path(args.out)
    result = json.loads(out.read_text())
    result["authorized_hydration"] = {
        "proof_level": "Declared prior fixture exchange hydration, not native source discovery or live inference",
        "channel": "conversation_history+client_conversation_history",
        "not_current_observation": True,
        "sources": [{"occurrence_id": row["occurrence_id"], "chat_scope": row["chat_scope"],
                     "role": row["role"], "body_sha256": hashlib.sha256(row["body"].encode()).hexdigest()}
                    for row in rows],
    }
    out.write_text(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
