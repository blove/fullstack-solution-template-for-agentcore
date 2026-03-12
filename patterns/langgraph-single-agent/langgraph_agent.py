# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import base64
import csv
import json
import logging
import os
import uuid
from pathlib import Path
from typing import Any, Literal, TypedDict

import uvicorn
from ag_ui.core import RunAgentInput
from ag_ui_langgraph import add_langgraph_fastapi_endpoint
from copilotkit import LangGraphAGUIAgent
from copilotkit import CopilotKitMiddleware
from fastapi import FastAPI
from langchain.agents import create_agent
from langchain.tools import ToolRuntime, tool
from langchain_aws import ChatBedrock
from langchain_core.messages import ToolMessage
from langgraph.types import Command
from langgraph.checkpoint.base import CheckpointTuple
from langgraph_checkpoint_aws import AgentCoreMemorySaver

BUILD_VERSION = "2026-03-12c"

logger = logging.getLogger("langgraph_single_agent")
if not logging.getLogger().handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
logger.setLevel(logging.INFO)
logger.info("[BOOT] langgraph_single_agent version=%s", BUILD_VERSION)

app = FastAPI()

ACTOR_ID_KEYS = ("actor_id", "actorId", "user_id", "userId", "sub")


class Todo(TypedDict):
    id: str
    title: str
    description: str
    emoji: str
    status: Literal["pending", "completed"]


class AgentState(TypedDict):
    todos: list[Todo]


def _csv_path() -> Path:
    return Path(__file__).resolve().parent / "db.csv"


def _load_rows() -> list[dict[str, str]]:
    with _csv_path().open(encoding="utf-8") as file:
        reader = csv.DictReader(file)
        rows: list[dict[str, str]] = []
        for row in reader:
            normalized = {key: value for key, value in row.items() if key is not None}
            extra = row.get(None)
            if extra:
                normalized["notes_extra"] = ", ".join(extra)
            rows.append(normalized)
    return rows


_CACHED_ROWS = _load_rows()


def _aggregate(
    rows: list[dict[str, str]],
    kind: str,
) -> list[dict[str, float | str]]:
    totals: dict[str, float] = {}
    for row in rows:
        row_type = (row.get("type") or "").strip().lower()
        include = (kind == "revenue" and row_type == "income") or (
            kind == "expenses" and row_type == "expense"
        )
        if not include:
            continue

        label = (row.get("subcategory") or row.get("category") or "Unknown").strip()
        amount_raw = (row.get("amount") or "0").replace(",", "").strip()
        try:
            amount = float(amount_raw)
        except ValueError:
            amount = 0.0
        totals[label] = totals.get(label, 0.0) + amount

    return [
        {"label": label, "value": round(value, 2)}
        for label, value in sorted(
            totals.items(), key=lambda item: item[1], reverse=True
        )
    ]


@tool
def query_data(query: str) -> dict[str, Any]:
    """
    Query the database. Always call this before showing a chart or graph.
    """
    # Copy row dicts so callers cannot mutate module-level cached data.
    rows = [dict(row) for row in _CACHED_ROWS]
    query_lower = query.lower()

    if "expense" in query_lower:
        selected_view = "expenses_by_subcategory"
        data = _aggregate(rows, "expenses")
    else:
        selected_view = "revenue_by_subcategory"
        data = _aggregate(rows, "revenue")

    result = {
        "rows": rows,
        "selected_view": selected_view,
        "data": data,
        "available_views": {
            "revenue_by_subcategory": _aggregate(rows, "revenue"),
            "expenses_by_subcategory": _aggregate(rows, "expenses"),
        },
        "notes": [
            "SQL statements are not executed.",
            "rows contains the full dataset from db.csv.",
            "Use selected_view + data to render the requested chart.",
        ],
        "raw_row_count": len(rows),
    }

    return result


@tool
def manage_todos(todos: list[Todo], runtime: ToolRuntime) -> Command:
    """
    Manage the current todos.
    """
    for todo in todos:
        if not todo.get("id"):
            todo["id"] = str(uuid.uuid4())

    return Command(
        update={
            "todos": todos,
            "messages": [
                ToolMessage(
                    content="Successfully updated todos",
                    tool_call_id=runtime.tool_call_id,
                    name="manage_todos",
                ),
            ],
        }
    )


@tool
def get_todos(runtime: ToolRuntime) -> list[Todo]:
    """
    Get the current todos.
    """
    todos = runtime.state.get("todos", [])
    return todos if isinstance(todos, list) else []


TODO_TOOLS = [manage_todos, get_todos]

SYSTEM_PROMPT = """
You are a helpful assistant that helps users understand CopilotKit and LangGraph used together.

Be brief in your explanations of CopilotKit and LangGraph, 1 to 2 sentences.

When demonstrating charts:
1. Always call the query_data tool to fetch data first.
2. Then call the relevant frontend chart tool (show_pie_chart or show_bar_chart) with chart-ready data.
3. Do not call query_data repeatedly with SQL variations in the same run.

Todo tools policy:
1. Only use get_todos or manage_todos when the user explicitly asks about todos, tasks, or app/canvas mode.
2. Never call get_todos or manage_todos for chart/theme/general Q&A requests.

Scheduling policy:
1. If the user asks to schedule a meeting, pick a meeting time, or explicitly asks for human-in-the-loop scheduling, call the frontend tool scheduleTime.
2. Provide scheduleTime arguments:
   - reasonForScheduling: short reason (about 3-5 words)
   - meetingDuration: use an integer number of minutes (default to 30 if user did not specify)
3. After calling scheduleTime, stop and wait for user interaction.

Stop condition:
- After calling the required frontend tool for the user request, stop calling tools and finish the run.
""".strip()


def decode_jwt_sub(authorization_header: str | None) -> str | None:
    if not authorization_header:
        return None

    parts = authorization_header.strip().split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None

    token_parts = parts[1].split(".")
    if len(token_parts) < 2:
        return None

    try:
        payload = token_parts[1]
        payload += "=" * ((4 - len(payload) % 4) % 4)
        decoded = base64.urlsafe_b64decode(payload.encode("utf-8"))
        sub = json.loads(decoded).get("sub")
        return sub if isinstance(sub, str) and sub else None
    except Exception:
        return None


def resolve_actor_id(
    input_data: RunAgentInput, authorization_header: str | None
) -> str | None:
    forwarded_props = (
        input_data.forwarded_props
        if isinstance(input_data.forwarded_props, dict)
        else {}
    )

    for key in ACTOR_ID_KEYS:
        value = forwarded_props.get(key)
        if isinstance(value, str) and value:
            return value

    return decode_jwt_sub(authorization_header)


def _build_model(streaming: bool) -> ChatBedrock:
    return ChatBedrock(
        model_id="us.anthropic.claude-sonnet-4-5-20250929-v1:0",
        temperature=0.1,
        max_tokens=16384,
        streaming=streaming,
        beta_use_converse_api=True,
    )


class ActorAwareLangGraphAgent(LangGraphAGUIAgent):
    async def run(self, input: RunAgentInput):
        """Inject actor_id from forwarded_props into config per-request."""
        actor_id = resolve_actor_id(input, None)
        if not actor_id:
            raise ValueError(
                "Missing actor identity. Provide forwardedProps.actor_id/user_id "
                "or include sub claim in forwarded props."
            )
        self.config = {"configurable": {"actor_id": actor_id}}
        logger.info("[AGUI] using add_langgraph_fastapi_endpoint path, actor_id=%s", actor_id)
        async for event in super().run(input):
            yield event


class _NoPatchEventProcessor:
    """EventProcessor that skips patch_orphan_tool_calls.

    CopilotKit frontend tool calls are intentionally orphaned — the frontend
    executes them client-side and adds the real ToolMessage on the next run.
    patch_orphan_tool_calls would inject fake error ToolMessages that conflict
    with the real results.

    Orphaned tool_calls are instead handled at merge time in
    ActorAwareLangGraphAgent.langgraph_default_merge_state.
    """

    def __init__(self, original_processor):
        self._original = original_processor

    def process_events(self, events):
        return self._original.process_events(events)

    def build_checkpoint_tuple(self, checkpoint_event, writes, channel_data, config):
        pending_writes = [
            (write.task_id, write.channel, write.value) for write in writes
        ]
        parent_config = None
        if checkpoint_event.parent_checkpoint_id:
            parent_config = {
                "configurable": {
                    "thread_id": config.thread_id,
                    "actor_id": config.actor_id,
                    "checkpoint_ns": config.checkpoint_ns,
                    "checkpoint_id": checkpoint_event.parent_checkpoint_id,
                }
            }

        checkpoint = checkpoint_event.checkpoint_data.copy()
        channel_values = {}
        for channel, version in checkpoint.get("channel_versions", {}).items():
            if (channel, version) in channel_data:
                channel_values[channel] = channel_data[(channel, version)]

        # NOTE: We intentionally skip patch_orphan_tool_calls here.
        # Orphaned frontend tool_calls are handled in langgraph_default_merge_state
        # by filtering stray ToolMessages instead.

        checkpoint["channel_values"] = channel_values

        return CheckpointTuple(
            config={
                "configurable": {
                    "thread_id": config.thread_id,
                    "actor_id": config.actor_id,
                    "checkpoint_ns": config.checkpoint_ns,
                    "checkpoint_id": checkpoint_event.checkpoint_id,
                }
            },
            checkpoint=checkpoint,
            metadata=checkpoint_event.metadata,
            parent_config=parent_config,
            pending_writes=pending_writes,
        )


class CopilotKitMemorySaver(AgentCoreMemorySaver):
    """AgentCoreMemorySaver that skips patch_orphan_tool_calls entirely."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.processor = _NoPatchEventProcessor(self.processor)


def _build_checkpointer() -> CopilotKitMemorySaver:
    memory_id = os.environ.get("MEMORY_ID")
    if not memory_id:
        raise ValueError("MEMORY_ID environment variable is required")

    return CopilotKitMemorySaver(
        memory_id=memory_id,
        region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
    )


def _build_agui_graph():
    graph = create_agent(
        model=_build_model(streaming=True),
        tools=[query_data, *TODO_TOOLS],
        checkpointer=_build_checkpointer(),
        middleware=[CopilotKitMiddleware()],
        state_schema=AgentState,
        system_prompt=SYSTEM_PROMPT,
    )
    return graph


@app.get("/ping")
async def ping() -> dict[str, str]:
    return {"status": "Healthy"}


@app.on_event("startup")
async def startup_event() -> None:
    try:
        graph = _build_agui_graph()
        agent = ActorAwareLangGraphAgent(
            name="LangGraphSingleAgent",
            description="LangGraph single agent exposed via AG-UI",
            graph=graph,
        )
        add_langgraph_fastapi_endpoint(app, agent, path="/invocations")
    except Exception as exc:
        logger.error("[STARTUP ERROR] %s", exc)
        raise


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)
