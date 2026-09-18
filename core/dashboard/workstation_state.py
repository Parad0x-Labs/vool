from __future__ import annotations

from typing import Any


def build_workstation_initial_state_payload(*, hooks: Any) -> dict[str, Any]:
    return (
    {
        "generated_at": None,
        "branding": hooks._branding_payload(),
        "stats": None,
        "mesh_overview": None,
        "learning_overview": None,
        "knowledge_overview": None,
        "memory_overview": None,
        "recent_activity": {
            "tasks": [],
            "responses": [],
            "learning": [],
        },
        "topics": [],
        "recent_posts": [],
        "recent_topic_claims": [],
        "task_event_stream": [],
        "agents": [],
        "trading_learning": {
            "topic_count": 0,
            "topics": [],
            "latest_summary": {},
            "latest_heartbeat": {},
            "lab_summary": {},
            "decision_funnel": {},
            "pattern_health": {},
            "calls": [],
            "missed_mooners": [],
            "hidden_edges": [],
            "discoveries": [],
            "flow": [],
            "lessons": [],
            "updates": [],
            "recent_posts": [],
        },
        "learning_lab": {
            "topic_count": 0,
            "active_topics": [],
        },
    }
    )
