"""VOOL agent teams: the chat starts agents; plain code coordinates them.

See ``docs/AGENT_TEAMS.md``. Modules: ``contract`` (briefs and the plan law), ``names`` (task and
importance names), ``limits`` (spend per agent and team), ``registry`` (durable state), ``lineage``
(which processes are whose), ``overlap`` (who touched whose files), ``advice`` (which to pause),
``report`` (honesty check and the 400-token cap), ``policy`` (never-do), ``gate`` (VOOL file-tool
writes), ``model_agent`` (model-loop agents on their own model), ``coordinator`` (the team).
"""
