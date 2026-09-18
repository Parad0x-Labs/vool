"""VOOL native coding assistant: ONE CodeTaskRuntime composed from the existing authorities.

Reconciliation (2026-09-03): this package carried two competing architectures -- an in-process
proposal loop (``runtime`` + ``workflow`` + the ``execute_proposal`` door, which crossed
``authorized_tool_execution`` directly with its own evidence ledger) and the served kernel
(``task_runtime`` executing contracted ``code.task.*`` calls through the ONE production door).
The served kernel won and the in-process loop is retired: one loader (the task journal), one
state machine (the nine-stage root-cause plan), one journal, one approval authority
(``code.task.propose``/``approve`` binding mutations byte-for-byte), one dispatch seam
(``dispatch_code_task_intent`` behind ``core.runtime_execution_tools``).

What the losing path contributed and survives here:

* ``contract``   -- the dialect-neutral proposal contract and its typed refusals
                   (``validate_proposal``); execution removed with the losing door.
* ``dialects``   -- cloud-native and local-text tool dialects normalizing to the ONE canonical
                   ``(intent, arguments)`` call (``canonical_call``).
* ``workflow`` invariants -- reproduced failure before any repair, owner read before propose,
                   rationale on every proposal, commands lawful only at evidence stages; all now
                   enforced by the runtime's stage machine itself.
* ``review``     -- the read-only ``code.review_evidence`` projection of the Blackbox journal.
"""
