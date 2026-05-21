"""update_plan tool — update a task tracking plan.

Mirrors TypeScript src/agents/tools/update-plan-tool.ts

Allows the agent to maintain a structured task plan in the session,
updating todo items as it works through them.
"""
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

TOOL_NAME = "update_plan"
TOOL_DESCRIPTION = (
    "Update the current task plan. Add, modify, or complete todo items to track progress "
    "on multi-step tasks. Use this to maintain a structured list of what needs to be done."
)


class UpdatePlanTool:
    """update_plan tool implementation."""

    name = TOOL_NAME
    description = TOOL_DESCRIPTION

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "todos": {
                    "type": "array",
                    "description": "List of todo items to set/update.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string", "description": "Unique id for this todo."},
                            "content": {"type": "string", "description": "Description of the task."},
                            "status": {
                                "type": "string",
                                "enum": ["pending", "in_progress", "completed", "cancelled"],
                                "description": "Current status of the todo.",
                            },
                        },
                        "required": ["id", "content", "status"],
                    },
                },
                "merge": {
                    "type": "boolean",
                    "description": "If true, merge into existing todos. If false, replace all.",
                    "default": True,
                },
            },
            "required": ["todos"],
        }

    async def execute(self, params: dict[str, Any], context: Any = None) -> dict[str, Any]:
        todos = params.get("todos", [])
        merge = params.get("merge", True)

        # Store plan in session metadata if session store is available
        try:
            session_key = (
                getattr(context, "session_key", None)
                or getattr(context, "SessionKey", None)
            )
            if session_key:
                from openclaw.agents.session_store import get_session_store
                store = get_session_store()
                if store and hasattr(store, "get_session"):
                    import asyncio
                    session = await store.get_session(session_key)
                    if session:
                        existing = (session.metadata or {}).get("plan_todos", [])
                        if merge:
                            existing_by_id = {t["id"]: t for t in existing if isinstance(t, dict)}
                            for todo in todos:
                                if isinstance(todo, dict) and todo.get("id"):
                                    existing_by_id[todo["id"]] = todo
                            new_todos = list(existing_by_id.values())
                        else:
                            new_todos = todos
                        metadata = dict(session.metadata or {})
                        metadata["plan_todos"] = new_todos
                        await store.patch_session(session_key, {"metadata": metadata})
        except Exception as exc:
            logger.debug("Could not persist plan todos: %s", exc)

        return {
            "ok": True,
            "todos": todos,
            "merge": merge,
        }
