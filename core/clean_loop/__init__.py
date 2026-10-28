"""The clean local path: a plain tool loop for local-model turns about the owner's own files or computer.

Opt-in (`VOOL_CLEAN_LOCAL_LOOP=1`), parallel to the front-door fast paths, for measurement first.
See docs/clean-local-loop.md. Files:

* `route.py`    -- should this turn take the clean path (flag, RED floor, ownership, topic, local brain)
* `brain.py`    -- which local model, called through the router's existing provider seam
* `loop.py`     -- the step loop: one JSON action per step, tolerant parse, step cap
* `tools.py`    -- the tool table over VOOL's machine intents, through the authorized boundary
* `prompt.py`   -- the short, static system prompt
* `registry.py` -- plug-in seams: extra tools, pre-steps, answer gates
* `hook.py`     -- the one runtime entry, `maybe_answer`
"""
